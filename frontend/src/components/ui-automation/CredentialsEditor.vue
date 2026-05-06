<template>
  <div class="creds-editor">
    <div class="creds-editor__header">
      <span class="creds-editor__title">
        <span class="i-carbon-password mr-1" />
        凭据（Fernet 加密存储；保存后无法再从界面读回明文）
      </span>
      <n-tag
        v-if="hasExisting && !clearExistingLocal && entries.length === 0"
        type="success"
        size="small"
        :bordered="false"
      >
        <template #icon><span class="i-carbon-locked" /></template>
        已有加密凭据
      </n-tag>
    </div>

    <p class="creds-editor__hint">{{ presetHint }}</p>

    <div v-if="entries.length > 0" class="creds-editor__rows">
      <div v-for="(entry, idx) in entries" :key="idx" class="creds-editor__row">
        <n-input
          :value="entry.key"
          placeholder="字段名（如 username）"
          size="small"
          class="creds-editor__key"
          @update:value="(v: string) => updateEntry(idx, 'key', v)"
        />
        <n-input
          :value="entry.value"
          type="password"
          show-password-on="click"
          placeholder="值（密码 / token）"
          size="small"
          class="creds-editor__val"
          @update:value="(v: string) => updateEntry(idx, 'value', v)"
        />
        <n-button
          size="small"
          quaternary
          circle
          @click="removeEntry(idx)"
        >
          <template #icon><span class="i-carbon-close" /></template>
        </n-button>
      </div>
    </div>

    <div class="creds-editor__actions">
      <n-button size="small" dashed @click="addEntry">
        <template #icon><span class="i-carbon-add" /></template>
        新增字段
      </n-button>
      <n-checkbox
        v-if="hasExisting"
        :checked="clearExistingLocal"
        @update:checked="onToggleClear"
      >
        清空已保存的凭据
      </n-checkbox>
      <span
        v-if="hasExisting && entries.length > 0 && !clearExistingLocal"
        class="creds-editor__warn"
      >
        <span class="i-carbon-information mr-1" />
        填入的字段会覆盖已有加密凭据中的同名字段
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NButton, NCheckbox, NInput, NTag } from "naive-ui";

interface CredentialEntry {
  key: string;
  value: string;
}

const props = defineProps<{
  entries: CredentialEntry[];
  clearExisting: boolean;
  hasExisting: boolean;
  presetHint?: string;
}>();

const emit = defineEmits<{
  "update:entries": [value: CredentialEntry[]];
  "update:clearExisting": [value: boolean];
}>();

const clearExistingLocal = computed(() => props.clearExisting);

function addEntry() {
  emit("update:entries", [...props.entries, { key: "", value: "" }]);
}

function removeEntry(idx: number) {
  const next = [...props.entries];
  next.splice(idx, 1);
  emit("update:entries", next);
}

function updateEntry(idx: number, field: "key" | "value", v: string) {
  const next = props.entries.map((e, i) => (i === idx ? { ...e, [field]: v } : e));
  emit("update:entries", next);
}

function onToggleClear(v: boolean) {
  emit("update:clearExisting", v);
  if (v) {
    emit("update:entries", []);
  }
}
</script>

<style scoped>
.creds-editor {
  background: var(--bg-subtle, rgba(0, 0, 0, 0.02));
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  margin-top: 8px;
}

.creds-editor__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.creds-editor__title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.creds-editor__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin: 0 0 10px;
  line-height: 1.5;
}

.creds-editor__rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.creds-editor__row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.creds-editor__key {
  flex: 0 0 160px;
}

.creds-editor__val {
  flex: 1;
}

.creds-editor__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
}

.creds-editor__warn {
  font-size: 11px;
  color: var(--text-tertiary);
  display: inline-flex;
  align-items: center;
}
</style>
