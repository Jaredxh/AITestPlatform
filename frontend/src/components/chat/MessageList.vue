<template>
  <div ref="containerRef" class="message-list" @scroll.passive="onScroll">
    <n-spin v-if="loading" class="flex-center h-full">
      <template #description>加载消息中...</template>
    </n-spin>

    <template v-else>
      <div
        v-if="messages.length === 0 && !hasStreamingOutput && !isStreaming"
        class="message-list__empty"
      >
        <span class="i-carbon-chat-bot text-5xl mb-3 block opacity-30" />
        <p class="text-base font-medium mb-1">开始你的 AI 对话</p>
        <p class="text-sm text-muted">输入消息或上传文档，AI 将为你提供帮助</p>
      </div>

      <div v-else class="message-list__inner">
        <message-bubble
          v-for="msg in messages"
          :key="msg.id"
          :message="msg"
          @plan-confirm="(p) => emit('plan-confirm', p)"
          @plan-cancel="(id) => emit('plan-cancel', id)"
          @task-badge-patch="(p) => emit('task-badge-patch', p)"
        />

        <div v-if="hasStreamingOutput" class="message-list__streaming">
          <div class="message-list__avatar">
            <n-avatar :size="32" round class="message-list__avatar-bg">AI</n-avatar>
          </div>
          <div class="message-list__bubble">
            <div v-if="streaming.infos.length > 0" class="message-list__infos">
              <span
                v-for="(info, i) in streaming.infos"
                :key="i"
                class="message-list__info-chip"
              >
                <span class="i-carbon-circle-dash mr-1" />{{ info }}
              </span>
            </div>

            <details
              v-if="streaming.reasoning"
              class="message-list__reasoning"
              :open="!streamingHtml"
            >
              <summary class="message-list__reasoning-head">
                <span class="i-carbon-thinking" />
                <span>{{ streamingHtml ? "已思考" : "深度思考中..." }}</span>
                <span class="message-list__reasoning-len">
                  {{ streaming.reasoning.length }} 字
                </span>
              </summary>
              <div class="message-list__reasoning-body">
                {{ streaming.reasoning }}
              </div>
            </details>

            <div
              v-if="streamingHtml"
              class="markdown-body"
              v-html="streamingHtml"
            />
            <div
              v-else-if="!streaming.reasoning"
              class="message-list__bubble--loading"
            >
              <span class="message-list__dot" style="animation-delay: 0ms" />
              <span class="message-list__dot" style="animation-delay: 150ms" />
              <span class="message-list__dot" style="animation-delay: 300ms" />
            </div>
            <span v-if="streamingHtml" class="message-list__cursor" />
          </div>
        </div>

        <div ref="bottomRef" class="message-list__bottom" />
      </div>
    </template>

    <transition name="fade">
      <button
        v-if="showScrollDownButton"
        type="button"
        class="message-list__scroll-down"
        @click="scrollToBottom(true)"
        title="滚动到底部"
      >
        <span class="i-carbon-chevron-down" />
      </button>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue";
import { NAvatar, NSpin } from "naive-ui";
import { marked } from "marked";
import DOMPurify from "dompurify";
import MessageBubble from "./MessageBubble.vue";
import type { ChatMessage } from "@/services/chat";
import type { StreamingState } from "@/composables/useChat";
import type {
  ExecutionPlanCard,
  TaskBadgeMeta,
} from "@/components/skills/types";

const props = defineProps<{
  messages: ChatMessage[];
  streaming: StreamingState;
  isStreaming: boolean;
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "plan-confirm", payload: {
    messageId: string;
    taskId: string;
    plan: ExecutionPlanCard;
  }): void;
  (e: "plan-cancel", messageId: string): void;
  (e: "task-badge-patch", payload: { messageId: string; patch: Partial<TaskBadgeMeta> }): void;
}>();

const containerRef = ref<HTMLElement | null>(null);
const bottomRef = ref<HTMLElement | null>(null);

/** 自动跟随滚动是否启用：用户向上滚动后会临时关闭 */
const autoFollow = ref(true);
const showScrollDownButton = ref(false);

marked.setOptions({ breaks: true, gfm: true });

const hasStreamingOutput = computed(
  () =>
    props.isStreaming &&
    !!props.streaming.sessionId &&
    (props.streaming.content.length > 0 ||
      props.streaming.reasoning.length > 0 ||
      props.streaming.infos.length > 0),
);

const streamingHtml = computed(() => {
  const text = props.streaming.content;
  if (!text) return "";
  // 流式 markdown：marked 容忍未闭合的语法（如 ```）会自动补全代码块；
  // 即使到达边界半个 token，也能渲染出阶段性视图。
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html);
});

function isNearBottom(threshold = 120) {
  const el = containerRef.value;
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

/** 标记本次滚动是程序触发，避免 onScroll 把 autoFollow 误关掉。 */
let suppressScrollEvent = false;

function onScroll() {
  if (suppressScrollEvent) {
    suppressScrollEvent = false;
    return;
  }
  const el = containerRef.value;
  if (!el) return;
  const near = isNearBottom();
  autoFollow.value = near;
  showScrollDownButton.value = !near;
}

function scrollToBottom(force = false) {
  if (!force && !autoFollow.value) return;
  nextTick(() => {
    const el = containerRef.value;
    if (!el) return;
    suppressScrollEvent = true;
    // 流式过程中用瞬时滚动，避免每次 token 都触发动画卡顿；
    // 切会话/初始化才用 smooth 居底。
    el.scrollTop = el.scrollHeight;
    if (force) {
      // 双重保险：DOM 还没排到底时再补一次
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    }
    autoFollow.value = true;
    showScrollDownButton.value = false;
  });
}

watch(
  () => props.messages.length,
  () => scrollToBottom(true),
);

watch(
  () => props.streaming.content,
  () => scrollToBottom(),
);

watch(
  () => props.streaming.reasoning,
  () => scrollToBottom(),
);

watch(
  () => props.streaming.sessionId,
  () => {
    // 切换会话或新一轮流开始时，强制滚到底部，确保新回答可见。
    scrollToBottom(true);
  },
);

watch(
  () => props.loading,
  (v) => {
    if (!v) scrollToBottom(true);
  },
);

onMounted(() => scrollToBottom(true));

defineExpose({ scrollToBottom });
</script>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  position: relative;
  background: var(--bg-page);
}

.message-list__empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--text-tertiary);
  padding: 40px 16px;
}

.message-list__inner {
  padding: 16px 0 8px;
  max-width: 920px;
  margin: 0 auto;
}

.message-list__streaming {
  display: flex;
  gap: 12px;
  padding: 8px 24px;
}

.message-list__avatar {
  flex-shrink: 0;
}

.message-list__avatar-bg {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
  color: #fff !important;
  font-weight: 600;
}

.message-list__bubble {
  position: relative;
  max-width: 75%;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 14px 14px 14px 4px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.65;
  color: var(--text-primary);
}

.message-list__bubble--loading {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
}

.message-list__dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: dot-bounce 1.2s infinite;
}

@keyframes dot-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
  30% { transform: translateY(-4px); opacity: 1; }
}

.message-list__infos {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.message-list__info-chip {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  padding: 2px 8px;
}

.message-list__reasoning {
  margin-bottom: 8px;
  border: 1px dashed var(--border-default);
  border-radius: 10px;
  padding: 6px 10px;
  background: color-mix(in srgb, var(--brand-primary, #6366f1) 5%, transparent);
}

.message-list__reasoning-head {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  list-style: none;
  font-size: 12px;
  color: var(--text-secondary);
}

.message-list__reasoning-head::-webkit-details-marker {
  display: none;
}

.message-list__reasoning-len {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary);
}

.message-list__reasoning-body {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  max-height: 220px;
  overflow-y: auto;
  line-height: 1.6;
}

.message-list__cursor {
  display: inline-block;
  width: 2px;
  height: 14px;
  background: var(--brand-primary);
  margin-left: 2px;
  animation: cursor-blink 1s steps(2, start) infinite;
  vertical-align: middle;
}

@keyframes cursor-blink {
  to { visibility: hidden; }
}

.message-list__scroll-down {
  position: absolute;
  right: 24px;
  bottom: 16px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  cursor: pointer;
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.08));
  z-index: 5;
  transition: transform var(--duration-fast) var(--easing-standard);
}

.message-list__scroll-down:hover {
  transform: translateY(-2px);
  color: var(--brand-primary);
  border-color: var(--brand-primary-border);
}

.message-list__bottom {
  height: 1px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
