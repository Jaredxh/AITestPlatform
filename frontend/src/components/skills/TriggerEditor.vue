<template>
  <div class="trigger-editor" :class="{ 'trigger-editor--disabled': disabled }">
    <div class="trigger-editor__list">
      <n-tag
        v-for="(t, idx) in modelValue"
        :key="`${t}-${idx}`"
        size="small"
        type="info"
        :bordered="false"
        :closable="!disabled"
        @close="removeAt(idx)"
      >
        {{ t }}
      </n-tag>
      <span v-if="modelValue.length === 0" class="trigger-editor__empty">
        暂无触发词，按下方添加
      </span>
    </div>
    <div class="trigger-editor__input">
      <n-input
        v-model:value="draft"
        :placeholder="placeholder"
        :disabled="disabled"
        size="small"
        @keydown.enter.prevent="commit"
      />
      <n-button
        size="small"
        type="primary"
        :disabled="disabled || !draft.trim()"
        @click="commit"
      >
        添加
      </n-button>
    </div>
    <div v-if="hint" class="trigger-editor__hint">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { NTag, NInput, NButton } from "naive-ui";

const props = withDefaults(
  defineProps<{
    modelValue: string[];
    disabled?: boolean;
    placeholder?: string;
    hint?: string;
    maxItems?: number;
  }>(),
  {
    disabled: false,
    placeholder: "输入触发词后回车，例如：跑用例",
    hint: "命中任意触发词后会激活该技能（大小写不敏感、子串匹配）。",
    maxItems: 30,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string[]];
}>();

const draft = ref("");

function commit() {
  const v = draft.value.trim();
  if (!v) return;
  if (props.modelValue.length >= props.maxItems) {
    draft.value = "";
    return;
  }
  if (props.modelValue.some((it) => it.toLowerCase() === v.toLowerCase())) {
    draft.value = "";
    return;
  }
  emit("update:modelValue", [...props.modelValue, v]);
  draft.value = "";
}

function removeAt(idx: number) {
  const next = props.modelValue.slice();
  next.splice(idx, 1);
  emit("update:modelValue", next);
}
</script>

<style scoped>
.trigger-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.trigger-editor__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 28px;
  align-items: center;
  padding: 4px;
  border-radius: var(--radius-md);
  background: var(--bg-page-soft);
  border: 1px dashed var(--border-default);
}

.trigger-editor__empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 0 6px;
}

.trigger-editor__input {
  display: flex;
  gap: 8px;
}

.trigger-editor__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.trigger-editor--disabled .trigger-editor__list {
  opacity: 0.6;
}
</style>
