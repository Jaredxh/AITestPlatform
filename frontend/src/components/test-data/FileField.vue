<template>
  <div class="file-field">
    <!-- 已上传态：展示文件名 + 体积 + 下载 / 替换 / 删除 -->
    <div v-if="hasFile" class="file-field__uploaded">
      <div class="file-field__file-info">
        <span class="file-field__icon i-carbon-document" />
        <div class="file-field__file-meta">
          <div class="file-field__file-name">
            <n-ellipsis :tooltip="false">{{ displayName }}</n-ellipsis>
          </div>
          <div class="file-field__file-detail">
            <span>{{ formatFileSize(fileSize) }}</span>
            <span v-if="fileMime" class="file-field__mime">{{ fileMime }}</span>
          </div>
        </div>
      </div>
      <div class="file-field__actions">
        <n-tooltip placement="top">
          <template #trigger>
            <n-button
              size="tiny"
              quaternary
              :loading="downloading"
              :disabled="!itemId"
              @click="handleDownload"
            >
              <template #icon><span class="i-carbon-download" /></template>
            </n-button>
          </template>
          下载
        </n-tooltip>
        <n-tooltip placement="top">
          <template #trigger>
            <n-button
              size="tiny"
              quaternary
              :disabled="disabled"
              @click="triggerPicker"
            >
              <template #icon><span class="i-carbon-renew" /></template>
            </n-button>
          </template>
          {{ itemId ? "替换（需先删除该物料再重新创建）" : "替换选中的文件" }}
        </n-tooltip>
      </div>
    </div>

    <!-- 未上传态：拖放 / 点击选择 -->
    <div
      v-else
      class="file-field__dropzone"
      :class="{ 'file-field__dropzone--active': dragOver }"
      @click="triggerPicker"
      @dragover.prevent="handleDragOver"
      @dragleave.prevent="handleDragLeave"
      @drop.prevent="handleDrop"
    >
      <span class="i-carbon-cloud-upload file-field__upload-icon" />
      <div class="file-field__hint">
        <strong>点击选择</strong> 或拖拽文件到此处
      </div>
      <div class="file-field__sub">
        单文件最大 {{ maxMB }}MB · 常见文档 / 图片 / 压缩包
      </div>
    </div>

    <input
      ref="pickerRef"
      type="file"
      class="file-field__input"
      :accept="accept"
      @change="handlePickerChange"
    />

    <p v-if="errorMsg" class="file-field__error">{{ errorMsg }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { NButton, NEllipsis, NTooltip, useMessage } from "naive-ui";
import { downloadFileItem, formatFileSize } from "@/services/testData";

/**
 * FileField — file 类型物料的上传 / 展示组件。
 *
 * 职责分工：
 * - 新建场景：父组件未保存的 item（没有 itemId），此组件选完文件后通过
 *   `@pick` 把 File 对象抛出，父组件再调 `uploadFileItemApi` 发 multipart 请求。
 * - 已存在场景（有 itemId）：展示文件名 + 大小 + 下载按钮；后端目前只支持
 *   "删除物料后重新创建"的替换流程，所以这里的"替换"按钮文案偏保守。
 *
 * 拖放支持：dragover / drop；本地校验体积和扩展名（快速反馈）。
 */
const props = defineProps<{
  itemId?: string | null;
  filePath?: string | null;
  fileSize?: number | null;
  fileMime?: string | null;
  disabled?: boolean;
  /** 单文件最大 MB，默认与后端 settings.TEST_DATA_MAX_FILE_SIZE 对齐（50MB） */
  maxMB?: number;
  /** input accept 规则（逗号分隔，例如 ".csv,.json,image/*"） */
  accept?: string;
}>();

const emit = defineEmits<{
  /** 用户选中 / 拖入文件。父组件拿到 File 对象自行决定何时上传 */
  pick: [file: File];
}>();

const message = useMessage();

const pickerRef = ref<HTMLInputElement | null>(null);
const dragOver = ref(false);
const downloading = ref(false);
const errorMsg = ref<string>("");

const maxMB = computed(() => props.maxMB ?? 50);
const maxBytes = computed(() => maxMB.value * 1024 * 1024);

const hasFile = computed(() => !!props.filePath);

const displayName = computed(() => {
  if (!props.filePath) return "未选择文件";
  // 后端存的是完整相对路径；只取 basename 展示
  const parts = props.filePath.split("/");
  const base = parts[parts.length - 1] ?? props.filePath;
  // uuid 前缀是后端 _sanitize_filename 拼的，去掉更好看
  const sep = "_";
  const idx = base.indexOf(sep);
  if (idx === 32 && /^[0-9a-f]+$/i.test(base.slice(0, idx))) {
    return base.slice(idx + 1);
  }
  return base;
});

// 与后端 _BLOCKED_EXTS 保持一致（只是本地快速提示，后端仍会二次校验）
const BLOCKED_EXTS = [
  ".exe", ".bat", ".cmd", ".sh", ".com", ".scr",
  ".msi", ".jar", ".ps1", ".dll", ".so",
];

function triggerPicker() {
  if (props.disabled) return;
  errorMsg.value = "";
  pickerRef.value?.click();
}

function handlePickerChange(ev: Event) {
  const target = ev.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) validateAndEmit(file);
  // 清掉 input 的值，允许下次选同一个文件
  target.value = "";
}

function handleDragOver() {
  if (props.disabled) return;
  dragOver.value = true;
}

function handleDragLeave() {
  dragOver.value = false;
}

function handleDrop(ev: DragEvent) {
  dragOver.value = false;
  if (props.disabled) return;
  const file = ev.dataTransfer?.files?.[0];
  if (file) validateAndEmit(file);
}

function validateAndEmit(file: File) {
  errorMsg.value = "";
  if (file.size <= 0) {
    errorMsg.value = "文件为空，无法上传";
    return;
  }
  if (file.size > maxBytes.value) {
    errorMsg.value = `文件超过 ${maxMB.value}MB 上限（${formatFileSize(file.size)}）`;
    return;
  }
  const lower = file.name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot) : "";
  if (BLOCKED_EXTS.includes(ext)) {
    errorMsg.value = `出于安全考虑，禁止上传 ${ext} 文件`;
    return;
  }
  emit("pick", file);
}

async function handleDownload() {
  if (!props.itemId) return;
  downloading.value = true;
  try {
    await downloadFileItem(props.itemId, displayName.value);
  } catch (err) {
    message.error(err instanceof Error ? err.message : "下载失败");
  } finally {
    downloading.value = false;
  }
}
</script>

<style scoped>
.file-field {
  width: 100%;
}

.file-field__uploaded {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-subtle, var(--bg-card));
}

.file-field__file-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.file-field__icon {
  font-size: 22px;
  color: var(--brand-primary);
  flex-shrink: 0;
}

.file-field__file-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.file-field__file-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  min-width: 0;
}

.file-field__file-detail {
  font-size: 12px;
  color: var(--text-tertiary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-field__mime {
  font-family: var(--font-mono, monospace);
  padding: 0 4px;
  border-radius: 3px;
  background: var(--bg-active);
  font-size: 11px;
}

.file-field__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.file-field__dropzone {
  padding: 20px 16px;
  border: 2px dashed var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-subtle, var(--bg-card));
  text-align: center;
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    background-color var(--duration-fast) var(--easing-standard);
}

.file-field__dropzone:hover,
.file-field__dropzone--active {
  border-color: var(--brand-primary);
  background: var(--brand-gradient-soft);
}

.file-field__upload-icon {
  font-size: 28px;
  color: var(--brand-primary);
  display: block;
  margin-bottom: 4px;
}

.file-field__hint {
  font-size: 13px;
  color: var(--text-primary);
}

.file-field__sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.file-field__input {
  display: none;
}

.file-field__error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--error-color, #d03050);
}
</style>
