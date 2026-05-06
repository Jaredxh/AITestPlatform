<template>
  <header class="chat-header">
    <div class="chat-header__left">
      <span class="i-carbon-chat-bot chat-header__icon" />
      <span class="chat-header__title">{{ session?.title || "AI 对话" }}</span>
      <n-tag
        v-if="currentModelLabel"
        size="small"
        :bordered="false"
        type="info"
        class="chat-header__model-tag"
      >
        <template #icon><span class="i-carbon-machine-learning-model" /></template>
        {{ currentModelLabel }}
      </n-tag>
      <n-tag
        size="small"
        :bordered="false"
        type="success"
        class="chat-header__model-tag"
      >
        <template #icon><span class="i-carbon-bot" /></template>
        Agent · 智能检索
      </n-tag>
    </div>

    <div class="chat-header__right">
      <n-select
        v-if="promptOptions.length > 0"
        :value="selectedPromptId"
        :options="promptOptions"
        size="small"
        placeholder="🎭 切换提示词"
        clearable
        class="chat-header__select chat-header__select--prompt"
        @update:value="handlePromptChange"
      />
      <n-select
        v-if="configs.length > 0"
        :value="selectedConfigId"
        :options="configOptions"
        size="small"
        placeholder="选择模型"
        class="chat-header__select chat-header__select--model"
        @update:value="$emit('update:selectedConfigId', $event)"
      />
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NSelect, NTag } from "naive-ui";
import type { ChatSession } from "@/services/chat";
import type { LLMConfigInfo } from "@/services/llm";
import type { PromptListItem } from "@/services/prompts";

const props = defineProps<{
  session: ChatSession | null;
  configs: LLMConfigInfo[];
  selectedConfigId: string | null;
  prompts: PromptListItem[];
  selectedPromptId: string | null;
}>();

const emit = defineEmits<{
  "update:selectedConfigId": [value: string];
  "update:selectedPromptId": [value: string | null];
}>();

const configOptions = computed(() =>
  props.configs.map((c) => ({
    label: `${c.name}（${c.model}）${c.is_default ? " · 默认" : ""}`,
    value: c.id,
  })),
);

const currentModelLabel = computed(() => {
  if (!props.selectedConfigId) {
    return props.session?.llm_config_name || "";
  }
  const cfg = props.configs.find((c) => c.id === props.selectedConfigId);
  if (!cfg) return props.session?.llm_config_name || "";
  return `${cfg.name} · ${cfg.model}`;
});

const promptOptions = computed(() =>
  props.prompts.map((p) => ({
    label: `${p.is_default ? "⭐ " : ""}${p.name}`,
    value: p.id,
  })),
);

function handlePromptChange(value: string | null) {
  emit("update:selectedPromptId", value);
}
</script>

<style scoped>
.chat-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-card);
  flex-shrink: 0;
  gap: 12px;
}

.chat-header__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.chat-header__icon {
  color: var(--brand-primary);
  font-size: 18px;
  flex-shrink: 0;
}

.chat-header__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}

.chat-header__model-tag {
  flex-shrink: 0;
}

.chat-header__right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.chat-header__select {
  min-width: 180px;
}

.chat-header__select--model {
  min-width: 200px;
}
</style>
