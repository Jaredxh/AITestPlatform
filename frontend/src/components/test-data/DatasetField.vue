<template>
  <div class="dataset-field">
    <div class="dataset-field__toolbar">
      <n-tag size="small" :type="validationType" :bordered="false">
        <template #icon>
          <span :class="validationIcon" />
        </template>
        {{ validationLabel }}
      </n-tag>
      <div class="dataset-field__toolbar-actions">
        <n-button size="tiny" quaternary :disabled="disabled" @click="formatJson">
          <template #icon><span class="i-carbon-clean" /></template>
          格式化
        </n-button>
        <n-button size="tiny" quaternary :disabled="disabled" @click="insertTemplate">
          <template #icon><span class="i-carbon-add" /></template>
          插入示例
        </n-button>
      </div>
    </div>

    <n-input
      v-model:value="draft"
      type="textarea"
      :placeholder="placeholder"
      :disabled="disabled"
      :autosize="{ minRows: 6, maxRows: 16 }"
      :status="error ? 'error' : undefined"
      class="dataset-field__textarea"
      @blur="handleBlur"
    />

    <p v-if="error" class="dataset-field__error">
      <span class="i-carbon-warning" />
      {{ error }}
    </p>
    <p v-else class="dataset-field__hint">
      支持 JSON 数组或对象；后端在执行时可通过 <code>platform_iter_dataset</code>
      按行遍历（数组）或按 key 取值（对象）。
    </p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { NButton, NInput, NTag } from "naive-ui";

/**
 * DatasetField — dataset 类型物料的 JSON 编辑器。
 *
 * 为了避免引入 Monaco / codemirror 这类 >500KB 的依赖，这里用 Naive UI 的
 * textarea + 前端 `JSON.parse` 做校验。对普通 dataset（几十行）完全够用；
 * 超大 JSON 建议用 CSV 导入（Task 8.8）。
 *
 * 对外协议：
 * - `modelValue` 是已解析好的 JS 值（object / array / null / ...）
 * - 用户输入时我们保留字符串草稿 `draft`；只有 JSON 合法时才把解析结果同步回父组件
 *   （反之保持父组件上一个合法值，不会传 undefined 过去污染数据）
 * - 校验状态通过 UI 自身的 Tag + 错误文字反馈，不对外抛事件
 */
const props = defineProps<{
  modelValue: unknown;
  disabled?: boolean;
  placeholder?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: unknown];
}>();

const placeholder =
  '示例：\n[\n  { "username": "alice", "age": 28 },\n  { "username": "bob", "age": 34 }\n]';

const draft = ref<string>("");
const error = ref<string>("");

function serialize(v: unknown): string {
  if (v === null || v === undefined) return "";
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return "";
  }
}

draft.value = serialize(props.modelValue);

watch(
  () => props.modelValue,
  (v) => {
    // 只有父组件主动换过值（而不是我们自己触发的）才同步回 draft
    try {
      const serialized = serialize(v);
      if (draft.value.trim() === "" && serialized.trim() === "") return;
      // 如果当前 draft 能 parse 成同样的值，就不覆盖用户的格式化偏好
      if (draft.value.trim()) {
        try {
          if (JSON.stringify(JSON.parse(draft.value)) === JSON.stringify(v)) {
            return;
          }
        } catch {
          /* draft 当前无效，直接覆盖 */
        }
      }
      draft.value = serialized;
    } catch {
      /* ignore */
    }
  },
);

watch(draft, (v) => {
  error.value = "";
  const trimmed = v.trim();
  if (!trimmed) {
    // 空值视为清空；父组件可用 clear_value_json 标志实际置空。
    if (props.modelValue !== null && props.modelValue !== undefined) {
      emit("update:modelValue", null);
    }
    return;
  }
  try {
    const parsed = JSON.parse(trimmed);
    emit("update:modelValue", parsed);
  } catch (err) {
    error.value = err instanceof Error ? `JSON 语法错误：${err.message}` : "JSON 语法错误";
  }
});

const validation = computed<"ok" | "empty" | "error">(() => {
  const t = draft.value.trim();
  if (!t) return "empty";
  return error.value ? "error" : "ok";
});

const validationType = computed(() => {
  switch (validation.value) {
    case "ok":
      return "success" as const;
    case "error":
      return "error" as const;
    default:
      return "default" as const;
  }
});

const validationLabel = computed(() => {
  switch (validation.value) {
    case "ok":
      return "JSON 有效";
    case "error":
      return "JSON 语法错误";
    default:
      return "暂无内容";
  }
});

const validationIcon = computed(() => {
  switch (validation.value) {
    case "ok":
      return "i-carbon-checkmark-filled";
    case "error":
      return "i-carbon-warning";
    default:
      return "i-carbon-information";
  }
});

function handleBlur() {
  // 失焦时尝试自动格式化（只对 valid json 做；无效时保留原样便于用户修正）
  if (validation.value === "ok") {
    formatJson();
  }
}

function formatJson() {
  const t = draft.value.trim();
  if (!t) return;
  try {
    const parsed = JSON.parse(t);
    draft.value = JSON.stringify(parsed, null, 2);
  } catch {
    /* 格式化失败（语法错），不动 */
  }
}

function insertTemplate() {
  if (draft.value.trim()) return;
  draft.value = [
    "[",
    '  { "username": "alice", "age": 28 },',
    '  { "username": "bob", "age": 34 }',
    "]",
  ].join("\n");
}
</script>

<style scoped>
.dataset-field {
  width: 100%;
}

.dataset-field__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.dataset-field__toolbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dataset-field__textarea :deep(textarea) {
  font-family: var(--font-mono, monospace);
  font-size: 12.5px;
  line-height: 1.6;
}

.dataset-field__error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--error-color, #d03050);
  display: flex;
  align-items: center;
  gap: 4px;
}

.dataset-field__hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.dataset-field__hint code {
  background: var(--bg-active);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
