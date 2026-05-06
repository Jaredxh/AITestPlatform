<template>
  <div class="random-field">
    <div class="random-field__row">
      <n-input
        :value="modelValue"
        placeholder="如 phone:CN / email / uuid / digits:8"
        :disabled="disabled"
        @update:value="handleUpdate"
      >
        <template #prefix>
          <span class="i-carbon-reset random-field__prefix" />
        </template>
      </n-input>
      <n-dropdown
        :options="templateOptions"
        trigger="click"
        placement="bottom-end"
        :disabled="disabled"
        @select="handlePick"
      >
        <n-button size="medium" :disabled="disabled">
          <template #icon><span class="i-carbon-list" /></template>
          选模板
        </n-button>
      </n-dropdown>
      <n-button
        type="primary"
        secondary
        :disabled="disabled || !modelValue"
        @click="runPreview"
      >
        <template #icon><span class="i-carbon-play" /></template>
        试跑
      </n-button>
    </div>

    <div v-if="preview" class="random-field__preview">
      <span class="random-field__preview-label">预览值：</span>
      <code class="random-field__preview-value">{{ preview }}</code>
      <n-button size="tiny" quaternary @click="runPreview">
        <template #icon><span class="i-carbon-renew" /></template>
      </n-button>
    </div>
    <p v-else class="random-field__hint">
      每次执行时后端会重新生成；这里的「试跑」只是本地预览，帮你确认模板格式。
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, h } from "vue";
import { NButton, NDropdown, NInput, useMessage } from "naive-ui";
import type { DropdownOption } from "naive-ui";
import { RANDOM_TEMPLATES, previewRandomTemplate } from "@/services/testData";

/**
 * RandomField — random 类型物料的模板输入器。
 *
 * 用户直接输入模板字符串（例如 `phone:CN`）；我们提供：
 * - 下拉菜单「选模板」→ 12 种常见预设（与后端 random_generator.py 保持对齐）
 * - 「试跑」按钮在本地生成一个样例，用户立即看到"大概长这样"
 *
 * 本地 preview 的实现见 services/testData.ts 的 `previewRandomTemplate`；
 * 和后端不保证字符级一致（比如 uuid 会用 crypto.randomUUID），但够让用户
 * 理解模板语义。
 */
const props = defineProps<{
  modelValue: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const message = useMessage();
const preview = ref<string>("");

const templateOptions = computed<DropdownOption[]>(() =>
  RANDOM_TEMPLATES.map((t) => ({
    key: t.template,
    label: () =>
      h(
        "div",
        { style: "display:flex;flex-direction:column;gap:2px;padding:2px 0;" },
        [
          h("div", { style: "font-weight:500;" }, [
            h(
              "code",
              {
                style:
                  "background:var(--bg-active);padding:0 4px;border-radius:3px;font-size:12px;margin-right:6px;",
              },
              t.template,
            ),
            t.label,
          ]),
          h(
            "div",
            { style: "font-size:12px;color:var(--text-tertiary);" },
            `示例：${t.example}`,
          ),
        ],
      ),
  })),
);

function handleUpdate(v: string) {
  emit("update:modelValue", v);
}

function handlePick(key: string) {
  emit("update:modelValue", key);
  // 选模板后顺手生成一条预览
  setTimeout(runPreview, 0);
}

function runPreview() {
  if (!props.modelValue) {
    message.warning("请先输入或选择一个模板");
    return;
  }
  try {
    const sample = previewRandomTemplate(props.modelValue);
    if (sample === props.modelValue) {
      message.warning(
        "未识别该模板；后端仍会按原值返回。可选的 prefix：phone / email / uuid / digits / hex / letters / alnum / username / timestamp",
      );
    }
    preview.value = sample;
  } catch (err) {
    message.error(err instanceof Error ? err.message : "预览失败");
  }
}
</script>

<style scoped>
.random-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.random-field__row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.random-field__row > :first-child {
  flex: 1;
  min-width: 0;
}

.random-field__prefix {
  color: var(--text-tertiary);
  font-size: 14px;
}

.random-field__preview {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-md);
  background: var(--bg-subtle, var(--bg-card));
  border: 1px dashed var(--border-subtle);
}

.random-field__preview-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.random-field__preview-value {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--brand-primary);
  word-break: break-all;
  flex: 1;
}

.random-field__hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.4;
}
</style>
