<template>
  <n-modal
    :show="show"
    preset="card"
    :title="title"
    :style="{ width: '640px' }"
    :mask-closable="!uploading"
    :closable="!uploading"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <!-- 阶段一：选文件 -->
    <div v-if="!report" class="import-dialog__body">
      <n-alert type="info" :show-icon="false" class="mb-3">
        <div class="text-sm leading-relaxed">
          <p class="mb-1 font-medium">导入规则</p>
          <ul class="list-disc pl-5 space-y-0.5 text-text-secondary">
            <li>「用例编号」留空 = 新增；填写但项目里查不到 = 也按新增处理。</li>
            <li>「用例编号」项目里能查到 = <strong>覆盖式更新</strong>（标题、模块、步骤、状态等全部按 Excel 内容刷新）。</li>
            <li>「模块路径」用 <code>/</code> 分隔层级，缺失的层级会自动创建。</li>
          </ul>
        </div>
      </n-alert>

      <div
        class="import-dropzone"
        :class="{ 'is-dragover': isDragover, 'is-empty': !pickedFile }"
        @dragover.prevent="isDragover = true"
        @dragleave.prevent="isDragover = false"
        @drop.prevent="handleDrop"
        @click="triggerFilePicker"
      >
        <input
          ref="fileInputRef"
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          class="hidden"
          @change="handleFileChange"
        />
        <template v-if="!pickedFile">
          <span class="i-carbon-cloud-upload text-3xl text-brand" />
          <div class="mt-2 text-sm text-text-primary">点击或拖拽 .xlsx 文件到此处</div>
          <div class="mt-1 text-xs text-text-tertiary">
            单文件 ≤ 10MB；首次使用建议先
            <a class="text-brand cursor-pointer" @click.stop="emit('downloadTemplate')">下载模板</a>
          </div>
        </template>
        <template v-else>
          <span class="i-carbon-document text-3xl text-brand" />
          <div class="mt-2 text-sm text-text-primary truncate" :title="pickedFile.name">
            {{ pickedFile.name }}
          </div>
          <div class="mt-1 text-xs text-text-tertiary">{{ formatBytes(pickedFile.size) }}</div>
          <n-button
            size="tiny"
            quaternary
            class="mt-2"
            @click.stop="clearFile"
          >
            重新选择
          </n-button>
        </template>
      </div>
    </div>

    <!-- 阶段二：导入结果 -->
    <div v-else class="import-dialog__body">
      <div class="import-result-summary">
        <div class="import-result-stat">
          <span class="import-result-stat__num text-success">{{ report.created }}</span>
          <span class="import-result-stat__label">新增</span>
        </div>
        <div class="import-result-stat">
          <span class="import-result-stat__num text-warning">{{ report.updated }}</span>
          <span class="import-result-stat__label">更新</span>
        </div>
        <div class="import-result-stat">
          <span class="import-result-stat__num">{{ report.skipped }}</span>
          <span class="import-result-stat__label">跳过</span>
        </div>
        <div class="import-result-stat">
          <span
            class="import-result-stat__num"
            :class="report.errors.length > 0 ? 'text-error' : 'text-text-tertiary'"
          >{{ report.errors.length }}</span>
          <span class="import-result-stat__label">错误</span>
        </div>
      </div>

      <n-alert
        v-if="report.created_modules.length > 0"
        type="info"
        :show-icon="false"
        class="mt-3"
      >
        <div class="text-xs">
          导入过程中按路径顺手创建了 {{ report.created_modules.length }} 个新模块：
          <span class="text-text-secondary">
            {{ report.created_modules.slice(0, 5).join("、") }}
            <span v-if="report.created_modules.length > 5">
              等共 {{ report.created_modules.length }} 项
            </span>
          </span>
        </div>
      </n-alert>

      <div v-if="report.errors.length > 0" class="mt-3">
        <div class="text-xs text-text-secondary mb-1">
          以下行被跳过（共 {{ report.errors.length }} 条）：
        </div>
        <n-scrollbar style="max-height: 220px">
          <div class="import-error-list">
            <div
              v-for="(err, idx) in report.errors"
              :key="idx"
              class="import-error-item"
            >
              <span class="import-error-item__row">第 {{ err.row }} 行</span>
              <span v-if="err.title" class="import-error-item__title">{{ err.title }}</span>
              <span class="import-error-item__msg">{{ err.message }}</span>
            </div>
          </div>
        </n-scrollbar>
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <template v-if="!report">
          <n-button :disabled="uploading" @click="emit('update:show', false)">取消</n-button>
          <n-button
            type="primary"
            :loading="uploading"
            :disabled="!pickedFile"
            @click="submitUpload"
          >
            <template #icon><span class="i-carbon-upload" /></template>
            开始导入
          </n-button>
        </template>
        <template v-else>
          <n-button @click="resetAndPickAnother">再导一份</n-button>
          <n-button type="primary" @click="emit('update:show', false)">完成</n-button>
        </template>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NAlert, NButton, NModal, NScrollbar, useMessage } from "naive-ui";
import {
  importTestcasesApi,
  type TestcaseImportReport,
} from "@/services/testcases";

const props = defineProps<{
  show: boolean;
  projectId: string | null;
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  (e: "imported", report: TestcaseImportReport): void;
  // 用户在 dropzone 文案里点了 "下载模板"，由父组件下载（避免组件依赖 service 太多）
  (e: "downloadTemplate"): void;
}>();

const message = useMessage();

const fileInputRef = ref<HTMLInputElement | null>(null);
const pickedFile = ref<File | null>(null);
const isDragover = ref(false);
const uploading = ref(false);
const report = ref<TestcaseImportReport | null>(null);

const title = computed(() => (report.value ? "导入完成" : "批量导入测试用例"));

watch(
  () => props.show,
  (visible) => {
    if (!visible) {
      // 关闭弹窗时延迟 reset，让关闭动画走完，避免内容闪一下
      setTimeout(() => {
        pickedFile.value = null;
        report.value = null;
        isDragover.value = false;
        if (fileInputRef.value) fileInputRef.value.value = "";
      }, 200);
    }
  },
);

function triggerFilePicker() {
  if (uploading.value) return;
  fileInputRef.value?.click();
}

function handleFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  applyFile(file);
}

function handleDrop(e: DragEvent) {
  isDragover.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  applyFile(file);
}

function applyFile(file: File) {
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    message.error("请选择 .xlsx 格式的 Excel 文件");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    message.error("文件超过 10MB 上限，请拆分后再导入");
    return;
  }
  pickedFile.value = file;
}

function clearFile() {
  pickedFile.value = null;
  if (fileInputRef.value) fileInputRef.value.value = "";
}

function resetAndPickAnother() {
  report.value = null;
  pickedFile.value = null;
  if (fileInputRef.value) fileInputRef.value.value = "";
}

async function submitUpload() {
  if (!pickedFile.value || !props.projectId) return;
  uploading.value = true;
  try {
    const res = await importTestcasesApi(props.projectId, pickedFile.value);
    if (res.success) {
      report.value = res.data;
      const r = res.data;
      // 三种典型口径：全成功 / 部分成功 / 全失败 —— 区分一下用户感知
      if (r.errors.length === 0) {
        message.success(`导入完成：新增 ${r.created} 条、更新 ${r.updated} 条`);
      } else if (r.created + r.updated > 0) {
        message.warning(
          `部分成功：新增 ${r.created} 条、更新 ${r.updated} 条，${r.errors.length} 行有错误`,
        );
      } else {
        message.error(`导入失败：${r.errors.length} 行错误，详情见弹窗下方`);
      }
      emit("imported", r);
    } else {
      message.error(res.message || "导入失败");
    }
  } catch (exc) {
    const msg = exc instanceof Error ? exc.message : "导入失败";
    message.error(msg);
  } finally {
    uploading.value = false;
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
</script>

<style scoped>
.import-dialog__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.import-dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 28px 20px;
  border: 1px dashed var(--border-default, #d0d7de);
  border-radius: var(--radius-md, 8px);
  background: var(--bg-subtle, #f7f8fa);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
  text-align: center;
}

.import-dropzone:hover,
.import-dropzone.is-dragover {
  border-color: var(--brand-primary, #2e6be6);
  background: var(--brand-primary-soft, rgba(46, 107, 230, 0.06));
}

.hidden {
  display: none;
}

.text-brand {
  color: var(--brand-primary, #2e6be6);
}

.text-success {
  color: var(--n-color-success, #18a058);
}

.text-warning {
  color: var(--n-color-warning, #f0a020);
}

.text-error {
  color: var(--n-color-error, #d03050);
}

.text-text-primary {
  color: var(--text-primary);
}

.text-text-secondary {
  color: var(--text-secondary);
}

.text-text-tertiary {
  color: var(--text-tertiary);
}

.import-result-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.import-result-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 0;
  background: var(--bg-subtle, #f7f8fa);
  border-radius: var(--radius-md, 8px);
}

.import-result-stat__num {
  font-size: 22px;
  font-weight: 600;
}

.import-result-stat__label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.import-error-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.import-error-item {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 8px;
  background: var(--bg-subtle, #fbf3f3);
  border-left: 2px solid var(--n-color-error, #d03050);
  font-size: 12px;
  line-height: 1.5;
}

.import-error-item__row {
  font-weight: 600;
  color: var(--n-color-error, #d03050);
}

.import-error-item__title {
  color: var(--text-secondary);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-error-item__msg {
  color: var(--text-secondary);
  flex: 1;
  word-break: break-all;
}
</style>
