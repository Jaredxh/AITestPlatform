<template>
  <div class="chat-view">
    <aside class="chat-view__side">
      <session-list
        :sessions="chat.sessions.value"
        :active-id="chat.currentSessionId.value"
        :loading="chat.isLoadingSessions.value"
        @select="chat.selectSession"
        @create="handleCreate"
        @delete="chat.deleteSession"
      />
    </aside>

    <section class="chat-view__main">
      <chat-header
        :session="chat.currentSession.value"
        :configs="llmConfigs"
        :selected-config-id="selectedConfigId"
        :prompts="chatPrompts"
        :selected-prompt-id="selectedPromptId"
        @update:selected-config-id="selectedConfigId = $event"
        @update:selected-prompt-id="handlePromptSelect"
      />

      <div class="chat-view__messages">
        <message-list
          ref="messageListRef"
          :messages="chat.messages.value"
          :streaming="chat.streaming.value"
          :is-streaming="chat.isStreaming.value"
          :loading="chat.isLoadingMessages.value"
        />
      </div>

      <div class="chat-view__input-wrap">
        <chat-input
          ref="chatInputRef"
          :disabled="!chat.currentSessionId.value"
          :is-streaming="chat.isStreaming.value"
          :pending-files="chat.pendingFiles.value"
          @send="handleSend"
          @stop="chat.stopGeneration"
          @add-file="chat.addFile"
          @remove-file="chat.removeFile"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { useChat } from "@/composables/useChat";
import { getLLMConfigsApi } from "@/services/llm";
import { getPromptsApi, getPromptDetailApi } from "@/services/prompts";
import type { LLMConfigInfo } from "@/services/llm";
import type { PromptListItem } from "@/services/prompts";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";
import SessionList from "@/components/chat/SessionList.vue";
import ChatHeader from "@/components/chat/ChatHeader.vue";
import MessageList from "@/components/chat/MessageList.vue";
import ChatInput from "@/components/chat/ChatInput.vue";

const chat = useChat();
const projectStore = useProjectStore();
const authStore = useAuthStore();
const messageListRef = ref<InstanceType<typeof MessageList> | null>(null);
const chatInputRef = ref<InstanceType<typeof ChatInput> | null>(null);

const llmConfigs = ref<LLMConfigInfo[]>([]);
const selectedConfigId = ref<string | null>(null);

const chatPrompts = ref<PromptListItem[]>([]);
const selectedPromptId = ref<string | null>(null);
const selectedPromptContent = ref<string | null>(null);

async function fetchConfigs() {
  try {
    const res = await getLLMConfigsApi();
    if (res.success) {
      llmConfigs.value = res.data;
      const defaultConfig = res.data.find((c) => c.is_default);
      if (defaultConfig) {
        selectedConfigId.value = defaultConfig.id;
      } else if (res.data.length > 0) {
        selectedConfigId.value = res.data[0].id;
      }
    }
  } catch {
    /* ignore */
  }
}

async function fetchChatPrompts() {
  const pid = projectStore.currentProjectId;
  if (!pid) {
    chatPrompts.value = [];
    return;
  }
  try {
    const res = await getPromptsApi(pid, "chat");
    if (res.success) {
      chatPrompts.value = res.data;
      const defaultPrompt = res.data.find((p) => p.is_default);
      if (defaultPrompt && !selectedPromptId.value) {
        await handlePromptSelect(defaultPrompt.id);
      }
    }
  } catch {
    /* ignore */
  }
}

function renderPromptContent(content: string): string {
  const ctx: Record<string, string> = {
    project_name: projectStore.currentProject?.name || "通用",
    user_name: authStore.user?.display_name || authStore.user?.username || "",
    current_date: new Date().toISOString().slice(0, 10),
  };
  return content.replace(/\{\{(.+?)\}\}/g, (full, key) => {
    const k = String(key).trim();
    return ctx[k] !== undefined ? ctx[k] : full;
  });
}

async function handlePromptSelect(promptId: string | null) {
  selectedPromptId.value = promptId;
  let rendered: string | null = null;
  if (promptId) {
    try {
      const res = await getPromptDetailApi(promptId);
      if (res.success) {
        rendered = renderPromptContent(res.data.content);
      }
    } catch {
      rendered = null;
    }
  }
  selectedPromptContent.value = rendered;
  if (chat.currentSessionId.value) {
    await chat.applySystemPrompt(rendered);
  }
}

async function handleCreate() {
  await chat.createNewSession(
    selectedConfigId.value || undefined,
    projectStore.currentProjectId || undefined,
    selectedPromptContent.value || undefined,
  );
  chatInputRef.value?.focus();
}

async function handleSend(text: string) {
  if (!chat.currentSessionId.value) {
    await chat.createNewSession(
      selectedConfigId.value || undefined,
      projectStore.currentProjectId || undefined,
      selectedPromptContent.value || undefined,
    );
  }
  await chat.sendMessage(text, selectedConfigId.value || undefined);
}

watch(
  () => projectStore.currentProjectId,
  () => {
    chat.loadSessions(projectStore.currentProjectId || undefined);
    fetchChatPrompts();
  },
);

onMounted(() => {
  fetchConfigs();
  fetchChatPrompts();
  chat.loadSessions(projectStore.currentProjectId || undefined);
});
</script>

<style scoped>
.chat-view {
  height: 100%;
  display: flex;
  background-color: var(--bg-page);
  overflow: hidden;
}

.chat-view__side {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-sider);
  border-right: 1px solid var(--border-subtle);
  overflow: hidden;
}

.chat-view__side > :deep(*) {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.chat-view__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: var(--bg-card);
  height: 100%;
  overflow: hidden;
}

.chat-view__messages {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.chat-view__input-wrap {
  flex-shrink: 0;
  background: var(--bg-card);
  position: relative;
  z-index: 1;
}
</style>
