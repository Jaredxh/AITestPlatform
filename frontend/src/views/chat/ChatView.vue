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
        <skill-activation-hint :event="chat.latestSkillActivation.value" />

        <!-- Phase 13 / Task 13.3 — 离线回来汇总卡 -->
        <div
          v-if="chat.pendingSummary.value && chat.pendingSummary.value.count > 0"
          class="chat-view__pending"
          @click="dismissPendingSummary"
          :title="'点击关闭提示'"
        >
          <span class="i-carbon-bell text-amber-500" />
          <span>
            你离开期间完成 {{ chat.pendingSummary.value.count }} 个任务，已追加到下方对话末尾
          </span>
          <button class="chat-view__pending-close" type="button">×</button>
        </div>

        <message-list
          ref="messageListRef"
          :messages="chat.messages.value"
          :streaming="chat.streaming.value"
          :is-streaming="chat.isStreaming.value"
          :loading="chat.isLoadingMessages.value"
          @plan-confirm="handlePlanConfirm"
          @plan-cancel="handlePlanCancel"
          @task-badge-patch="handleTaskBadgePatch"
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
import { ref, onMounted, onBeforeUnmount, watch } from "vue";
import { useChat } from "@/composables/useChat";
import type {
  ExecutionPlanCard,
  TaskBadgeMeta,
} from "@/components/skills/types";
// 历史保留：``useSkillSelection`` / ``activate-manual`` 接口仍存在，但 chat
// header 已不再暴露"手动多选 skill"按钮——技能激活完全交由 SkillRouter
// 自动 (always / trigger / agent_callable) 决定，避免普通用户误以为必须先
// 手动勾选才能让 AI 调技能。
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
import SkillActivationHint from "@/components/chat/SkillActivationHint.vue";

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

// ─── Phase 13 / Task 13.3 — ConfirmationCard / TaskBadge 事件回调 ──────

function handlePlanConfirm(payload: {
  messageId: string;
  taskId: string;
  plan: ExecutionPlanCard;
}) {
  const sid = chat.currentSessionId.value;
  if (!sid) return;
  chat.applyPlanConfirmation(sid, payload);
}

function handlePlanCancel(messageId: string) {
  const sid = chat.currentSessionId.value;
  if (!sid) return;
  chat.applyPlanCancel(sid, messageId);
}

function handleTaskBadgePatch(payload: {
  messageId: string;
  patch: Partial<TaskBadgeMeta>;
}) {
  const sid = chat.currentSessionId.value;
  if (!sid) return;
  chat.applyTaskBadgePatchByMessage(sid, payload.messageId, payload.patch);
}

function dismissPendingSummary() {
  chat.clearPendingSummary();
}

watch(
  () => chat.currentSessionId.value,
  async (newId, oldId) => {
    if (oldId) chat.unsubscribeSystemEvents(oldId);
    if (newId) {
      // 切到新 session：订阅 SSE + 加载离线汇总 + 把所有非终态 TaskBadge
      // 拉一次最新态（防止重连漏过 task_status）。
      chat.subscribeSystemEvents(newId);
      await chat.loadPendingSummary(newId);
      await chat.refreshTaskBadgesForSession(newId);
    }
  },
);

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

onBeforeUnmount(() => {
  // 离开 ChatView 时主动断开所有 system-events SSE，避免后台 zombie 连接。
  for (const s of chat.sessions.value) {
    chat.unsubscribeSystemEvents(s.id);
  }
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
  position: relative;
}

.chat-view__input-wrap {
  flex-shrink: 0;
  background: var(--bg-card);
  position: relative;
  z-index: 1;
}

/* Phase 13 / Task 13.3 — 离线回来汇总卡（顶部黄色 banner） */
.chat-view__pending {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px auto 0;
  padding: 6px 14px;
  font-size: 12px;
  background: color-mix(in srgb, var(--brand-warning, #f0a020) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--brand-warning, #f0a020) 30%, transparent);
  border-radius: 8px;
  color: var(--text-primary);
  max-width: 920px;
  cursor: pointer;
}
.chat-view__pending-close {
  margin-left: auto;
  background: transparent;
  border: none;
  font-size: 16px;
  line-height: 1;
  color: var(--text-tertiary);
  cursor: pointer;
}
</style>
