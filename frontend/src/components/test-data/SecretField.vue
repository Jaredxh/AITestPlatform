<template>
  <div class="secret-field">
    <!-- 展示区：已有密值 -->
    <div v-if="displayMode" class="secret-field__display">
      <div class="secret-field__mask">
        <template v-if="revealed">
          <span class="secret-field__value">{{ revealedValue }}</span>
        </template>
        <template v-else-if="hasSecret">
          <span class="secret-field__mask-dots">●●●●●●●●</span>
          <n-tag size="tiny" :bordered="false" type="warning">已加密</n-tag>
        </template>
        <template v-else>
          <n-tag size="tiny" :bordered="false">未设置</n-tag>
        </template>
      </div>
      <div class="secret-field__actions">
        <n-tooltip placement="top">
          <template #trigger>
            <n-button
              v-if="hasSecret && !revealed"
              size="tiny"
              quaternary
              :loading="revealing"
              :disabled="!itemId"
              @click="handleReveal"
            >
              <template #icon><span class="i-carbon-view" /></template>
            </n-button>
          </template>
          查看明文（将记录审计日志）
        </n-tooltip>
        <n-tooltip placement="top">
          <template #trigger>
            <n-button
              v-if="revealed"
              size="tiny"
              quaternary
              @click="handleHide"
            >
              <template #icon><span class="i-carbon-view-off" /></template>
            </n-button>
          </template>
          收起明文
        </n-tooltip>
        <n-tooltip placement="top">
          <template #trigger>
            <n-button
              size="tiny"
              quaternary
              :disabled="disabled"
              @click="editing = true"
            >
              <template #icon><span class="i-carbon-edit" /></template>
            </n-button>
          </template>
          {{ hasSecret ? "重新设置" : "设置密值" }}
        </n-tooltip>
      </div>
    </div>

    <!-- 编辑区：输入新值 -->
    <div v-else class="secret-field__edit">
      <n-input
        v-model:value="draft"
        type="password"
        show-password-on="click"
        :placeholder="placeholder"
        :disabled="disabled"
        :maxlength="maxLength"
        @keyup.enter="handleSave"
      />
      <div class="secret-field__edit-actions">
        <n-button size="small" @click="handleCancelEdit">取消</n-button>
        <n-button
          type="primary"
          size="small"
          :disabled="!canSave"
          @click="handleSave"
        >
          {{ itemId ? "保存" : "确认" }}
        </n-button>
      </div>
      <p class="secret-field__tip">
        密值会被 Fernet 加密后入库；LLM 对话上下文<strong>永远看不到明文</strong>，
        执行时通过 <code>platform_get_secret</code> 工具按需取用。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { NButton, NInput, NTag, NTooltip, useMessage } from "naive-ui";
import { revealItemApi } from "@/services/testData";

/**
 * SecretField — secret 类型物料的专属输入组件。
 *
 * 两种形态：
 * - displayMode（has_secret_value === true 且当前不在编辑）：遮蔽显示 + reveal 按钮 + 改密按钮
 * - editMode（新建 / 点击改密）：password 输入框 + 保存/取消
 *
 * 保存时通过 @save 抛出明文值给父组件，由父组件调 createItem / updateItem。
 * 这样组件本身不用知道是 "创建" 还是 "更新"，也就不用拿到 setId / itemId 再发请求。
 */
const props = defineProps<{
  /** 已有 item 的 id；没有 id 时 reveal 按钮隐藏 */
  itemId?: string | null;
  /** 已存在加密值（来自后端 TestDataItemResponse.has_secret_value） */
  hasSecret: boolean;
  /** 父组件传入的"刚刚在编辑器中已填写但还未保存"的明文（用于连续编辑） */
  pendingValue?: string | null;
  placeholder?: string;
  disabled?: boolean;
  maxLength?: number;
}>();

const emit = defineEmits<{
  /** 用户点击保存时抛出明文值；父组件负责发 API */
  save: [plaintext: string];
  /** 用户取消编辑（未保存过的新 item，父组件可能要把 draft 清掉） */
  cancel: [];
}>();

const message = useMessage();

const editing = ref(false);
const draft = ref(props.pendingValue ?? "");
const revealed = ref(false);
const revealedValue = ref<string | null>(null);
const revealing = ref(false);

const displayMode = computed(() => !editing.value && props.hasSecret);

const canSave = computed(() => draft.value.length > 0 && !props.disabled);

watch(
  () => props.pendingValue,
  (v) => {
    draft.value = v ?? "";
  },
);

// 如果原本没有密值（比如新建的 item 还没填过），直接进入编辑态，更符合直觉。
watch(
  () => props.hasSecret,
  (hasSecret) => {
    if (!hasSecret) {
      editing.value = false;
    }
  },
  { immediate: false },
);

async function handleReveal() {
  if (!props.itemId) return;
  revealing.value = true;
  try {
    const res = await revealItemApi(props.itemId);
    if (res.success) {
      revealedValue.value = res.data.value_secret ?? "";
      revealed.value = true;
    }
  } catch (err) {
    message.error(
      err instanceof Error ? err.message : "读取明文失败，可能没有 reveal 权限",
    );
  } finally {
    revealing.value = false;
  }
}

function handleHide() {
  revealed.value = false;
  revealedValue.value = null;
}

function handleSave() {
  if (!canSave.value) return;
  emit("save", draft.value);
  editing.value = false;
  // 保存完不保留明文在内存，减少泄漏面
  draft.value = "";
  handleHide();
}

function handleCancelEdit() {
  editing.value = false;
  draft.value = "";
  emit("cancel");
}

defineExpose({
  /** 父组件强制让组件进入编辑态（比如"新建 item"弹窗打开时） */
  startEditing() {
    editing.value = true;
  },
});
</script>

<style scoped>
.secret-field {
  width: 100%;
}

.secret-field__display {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-subtle, var(--bg-card));
  min-height: 34px;
}

.secret-field__mask {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.secret-field__mask-dots {
  letter-spacing: 2px;
  color: var(--text-secondary);
  font-family: var(--font-mono, monospace);
}

.secret-field__value {
  font-family: var(--font-mono, monospace);
  color: var(--text-primary);
  word-break: break-all;
  max-width: 100%;
}

.secret-field__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.secret-field__edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.secret-field__edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.secret-field__tip {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.secret-field__tip code {
  background: var(--bg-active);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
