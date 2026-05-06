<template>
  <div class="chat-input">
    <div v-if="pendingFiles.length > 0" class="chat-input__files">
      <div
        v-for="pf in pendingFiles"
        :key="pf.id"
        class="chat-input__file"
        :class="{ 'is-error': pf.status === 'error' }"
      >
        <span :class="fileIcon(pf)" class="text-base" />
        <span class="chat-input__file-name">{{ pf.file.name }}</span>
        <span v-if="pf.status === 'uploading'" class="i-carbon-renew animate-spin chat-input__file-spinner" />
        <span v-else-if="pf.status === 'done'" class="i-carbon-checkmark text-green-500" />
        <span v-else class="text-xs text-red-500">{{ pf.error }}</span>
        <button
          type="button"
          class="chat-input__file-close"
          @click="$emit('removeFile', pf.id)"
          aria-label="移除"
        >
          <span class="i-carbon-close" />
        </button>
      </div>
    </div>

    <div
      class="chat-input__shell"
      :class="{ 'is-dragging': isDragging, 'is-disabled': disabled }"
      @dragover.prevent="isDragging = true"
      @dragleave="isDragging = false"
      @drop.prevent="handleDrop"
    >
      <div v-if="isDragging" class="chat-input__drop-hint">
        <span class="i-carbon-cloud-upload mr-1" />
        松开鼠标上传文件
      </div>

      <textarea
        ref="textareaRef"
        v-model="inputText"
        class="chat-input__textarea"
        :rows="1"
        :placeholder="disabled ? '请先创建或选择一个对话' : '输入消息（Shift+Enter 换行）...'"
        :disabled="disabled"
        @keydown="handleKeydown"
        @input="autoResize"
      />

      <div class="chat-input__toolbar">
        <div class="chat-input__tools">
          <n-tooltip trigger="hover" placement="top">
            <template #trigger>
              <button
                type="button"
                class="chat-input__tool-btn"
                :disabled="disabled"
                @click="triggerFileInput"
              >
                <span class="i-carbon-attachment" />
              </button>
            </template>
            上传文档（PDF / Word .doc/.docx / 图片）
          </n-tooltip>

          <n-tooltip trigger="hover" placement="top">
            <template #trigger>
              <span class="chat-input__tool-hint">
                <span class="i-carbon-bot" />
                <span class="chat-input__tool-label">智能 Agent</span>
              </span>
            </template>
            AI 会自主判断是否需要联网检索，你只管提问即可
          </n-tooltip>

          <slot name="tools" />
        </div>

        <input
          ref="fileInputRef"
          type="file"
          class="hidden"
          accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.gif,.webp"
          multiple
          @change="handleFileSelect"
        />

        <div class="chat-input__send">
          <span v-if="!isStreaming && inputText" class="chat-input__hint">
            按 <kbd>↵</kbd> 发送 · <kbd>⇧↵</kbd> 换行
          </span>
          <button
            v-if="isStreaming"
            type="button"
            class="chat-input__send-btn is-stop"
            @click="$emit('stop')"
            aria-label="停止生成"
          >
            <span class="i-carbon-stop-filled" />
          </button>
          <button
            v-else
            type="button"
            class="chat-input__send-btn"
            :disabled="!canSend"
            @click="handleSend"
            aria-label="发送"
          >
            <span class="i-carbon-send-filled" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick } from "vue";
import { NTooltip } from "naive-ui";
import type { PendingFile } from "@/composables/useChat";

const props = defineProps<{
  disabled: boolean;
  isStreaming: boolean;
  pendingFiles: PendingFile[];
}>();

const emit = defineEmits<{
  send: [text: string];
  stop: [];
  addFile: [file: File];
  removeFile: [id: string];
}>();

const inputText = ref("");
const isDragging = ref(false);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const fileInputRef = ref<HTMLInputElement | null>(null);

const allFilesReady = computed(() =>
  props.pendingFiles.length === 0 ||
  props.pendingFiles.every((f) => f.status !== "uploading"),
);

const canSend = computed(
  () => inputText.value.trim().length > 0 && !props.disabled && !props.isStreaming && allFilesReady.value,
);

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
}

function handleSend() {
  if (!canSend.value) return;
  emit("send", inputText.value.trim());
  inputText.value = "";
  nextTick(autoResize);
}

function autoResize() {
  const el = textareaRef.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

function triggerFileInput() {
  fileInputRef.value?.click();
}

function handleFileSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files) {
    for (const file of input.files) {
      emit("addFile", file);
    }
    input.value = "";
  }
}

function handleDrop(e: DragEvent) {
  isDragging.value = false;
  const files = e.dataTransfer?.files;
  if (files) {
    for (const file of files) {
      emit("addFile", file);
    }
  }
}

function fileIcon(pf: PendingFile) {
  const name = pf.file.name.toLowerCase();
  if (name.endsWith(".pdf")) return "i-carbon-document-pdf text-red-500";
  if (name.endsWith(".doc") || name.endsWith(".docx")) return "i-carbon-document text-blue-500";
  if (pf.file.type.startsWith("image/")) return "i-carbon-image text-green-500";
  return "i-carbon-document-blank";
}

defineExpose({
  focus: () => textareaRef.value?.focus(),
});
</script>

<style scoped>
.chat-input {
  padding: 12px 24px 18px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-subtle);
}

.chat-input__files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.chat-input__file {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: var(--radius-md);
  font-size: 12px;
  background: var(--bg-page-soft);
  border: 1px solid var(--border-subtle);
}

.chat-input__file.is-error {
  background: rgba(208, 48, 80, 0.06);
  border-color: rgba(208, 48, 80, 0.4);
  color: var(--color-error, #d03050);
}

.chat-input__file-name {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-input__file-spinner {
  color: var(--brand-primary);
}

.chat-input__file-close {
  background: none;
  border: 0;
  cursor: pointer;
  opacity: 0.5;
  padding: 0;
  display: inline-flex;
  align-items: center;
  color: inherit;
}

.chat-input__file-close:hover {
  opacity: 1;
}

.chat-input__shell {
  position: relative;
  border: 1.5px solid var(--border-default);
  border-radius: var(--radius-lg, 14px);
  background: var(--bg-card);
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    box-shadow var(--duration-fast) var(--easing-standard);
}

.chat-input__shell:focus-within {
  border-color: var(--brand-primary);
  box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.12);
}

.chat-input__shell.is-dragging {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.chat-input__shell.is-disabled {
  opacity: 0.65;
  pointer-events: none;
}

.chat-input__drop-hint {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(99, 102, 241, 0.06);
  color: var(--brand-primary);
  font-weight: 500;
  font-size: 13px;
  z-index: 1;
  pointer-events: none;
  border-radius: inherit;
}

.chat-input__textarea {
  width: 100%;
  resize: none;
  border: 0;
  outline: none;
  background: transparent;
  padding: 12px 16px 4px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
  max-height: 200px;
  font-family: inherit;
}

.chat-input__textarea::placeholder {
  color: var(--text-tertiary);
}

.chat-input__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px 8px;
}

.chat-input__tools {
  display: flex;
  align-items: center;
  gap: 4px;
}

.chat-input__tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  border: 0;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    color var(--duration-fast) var(--easing-standard);
}

.chat-input__tool-btn:hover:not(:disabled) {
  background: var(--bg-active);
  color: var(--text-secondary);
}

.chat-input__tool-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-input__tool-label {
  font-size: 12px;
}

.chat-input__tool-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  user-select: none;
  cursor: help;
}

.chat-input__send {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-input__hint {
  font-size: 11px;
  color: var(--text-tertiary);
}

.chat-input__hint kbd {
  display: inline-block;
  padding: 0 5px;
  font-size: 10px;
  font-family: ui-monospace, monospace;
  background: var(--bg-page-soft);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text-secondary);
}

.chat-input__send-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 0;
  background: var(--brand-primary);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 14px;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    transform var(--duration-fast) var(--easing-standard),
    opacity var(--duration-fast) var(--easing-standard);
}

.chat-input__send-btn:hover:not(:disabled) {
  background: var(--brand-primary-hover);
  transform: scale(1.05);
}

.chat-input__send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.chat-input__send-btn.is-stop {
  background: var(--color-error, #d03050);
}

.hidden {
  display: none;
}
</style>
