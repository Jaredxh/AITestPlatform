<template>
  <n-modal
    v-model:show="visible"
    preset="card"
    :style="{ width: '720px' }"
    title="导入技能包"
    :mask-closable="!busy"
    :segmented="{ content: true }"
  >
    <n-tabs v-model:value="activeTab" type="segment" :disabled="busy">
      <!-- ZIP 上传 -->
      <n-tab-pane name="zip" tab="上传 ZIP">
        <div
          class="ski-dropzone"
          :class="{ 'ski-dropzone--active': dragOver, 'ski-dropzone--file': !!zipFile }"
          @click="triggerPicker"
          @dragover.prevent="dragOver = true"
          @dragleave.prevent="dragOver = false"
          @drop.prevent="handleDrop"
        >
          <template v-if="!zipFile">
            <span class="i-carbon-document-import ski-dropzone__icon" />
            <div class="ski-dropzone__hint">
              <strong>点击选择</strong> 或拖拽 SKILL ZIP 到此处
            </div>
            <div class="ski-dropzone__sub">
              须包含 <code>SKILL.md</code>；体积 ≤ 5 MB；附件 ≤ 50 个、每个 ≤ 1 MB
            </div>
          </template>
          <template v-else>
            <span class="i-carbon-document ski-dropzone__icon" />
            <div class="ski-dropzone__file">
              <div class="ski-dropzone__file-name">{{ zipFile.name }}</div>
              <div class="ski-dropzone__sub">{{ formatSize(zipFile.size) }}</div>
            </div>
            <n-button size="tiny" quaternary :disabled="busy" @click.stop="resetZip">
              <template #icon><span class="i-carbon-close" /></template>
            </n-button>
          </template>
        </div>
        <input
          ref="pickerRef"
          type="file"
          class="ski-input"
          accept=".zip,application/zip,application/x-zip-compressed"
          @change="onPicker"
        />
        <div class="ski-actions">
          <n-button
            type="primary"
            :loading="busy && activeTab === 'zip'"
            :disabled="!zipFile || busy"
            @click="submitZip"
          >
            <template #icon><span class="i-carbon-upload" /></template>
            上传并解析
          </n-button>
        </div>
      </n-tab-pane>

      <!-- URL 导入 -->
      <n-tab-pane name="url" tab="URL 导入">
        <n-form label-placement="top" :show-feedback="false" class="ski-url-form">
          <n-form-item label="ZIP / SKILL.md / Git 仓库地址">
            <n-input
              v-model:value="urlValue"
              placeholder="https://example.com/skill.zip 或 git+https://github.com/.../repo.git"
              :disabled="busy"
            />
          </n-form-item>
          <n-form-item label="Git 分支 / Tag（可选）">
            <n-input
              v-model:value="urlRef"
              placeholder="main"
              :disabled="busy"
            />
          </n-form-item>
          <div class="ski-actions">
            <n-button
              type="primary"
              :loading="busy && activeTab === 'url'"
              :disabled="!urlValue.trim() || busy"
              @click="submitUrl"
            >
              <template #icon><span class="i-carbon-cloud-download" /></template>
              拉取并导入
            </n-button>
          </div>
        </n-form>
      </n-tab-pane>

      <!-- 内置模板 -->
      <n-tab-pane name="template" tab="从内置模板">
        <n-empty
          description="内置模板（system_*）已在新建项目时自动注入。"
          class="ski-empty"
        >
          <template #extra>
            <n-button size="small" @click="visible = false">前往列表查看</n-button>
          </template>
        </n-empty>
      </n-tab-pane>
    </n-tabs>

    <!-- 解析预览 -->
    <div v-if="preview" class="ski-preview">
      <div class="ski-preview__head">
        <div class="ski-preview__title">
          <span class="i-carbon-package" />
          <strong>{{ preview.name }}</strong>
          <code class="ski-preview__slug">{{ preview.slug }}</code>
          <safety-badge :status="preview.safety_status" />
        </div>
        <n-tag size="tiny" :bordered="false">v{{ preview.semantic_version }}</n-tag>
      </div>
      <div class="ski-preview__desc">{{ preview.description }}</div>
      <div class="ski-preview__row">
        <span class="ski-preview__label">触发词</span>
        <div class="ski-preview__tags">
          <n-tag
            v-for="t in preview.triggers"
            :key="t"
            size="tiny"
            type="info"
            :bordered="false"
          >
            {{ t }}
          </n-tag>
          <span v-if="preview.triggers.length === 0" class="text-muted">—</span>
        </div>
      </div>
      <div class="ski-preview__row">
        <span class="ski-preview__label">附件</span>
        <span>{{ preview.attachments.length }} 个</span>
      </div>
      <div class="ski-preview__row">
        <span class="ski-preview__label">激活模式</span>
        <span>{{ activationLabel(preview.activation_mode) }}</span>
      </div>
      <div v-if="preview.safety_findings.length > 0" class="ski-findings">
        <div class="ski-findings__title">安全扫描发现：</div>
        <ul>
          <li v-for="(f, i) in preview.safety_findings" :key="i">
            <n-tag size="tiny" :type="f.severity === 'high' ? 'error' : 'warning'" :bordered="false">
              {{ f.severity }}
            </n-tag>
            <span class="ml-2">{{ f.type }}</span>
            <span v-if="f.snippet" class="ski-findings__snippet">{{ f.snippet }}</span>
          </li>
        </ul>
      </div>
      <n-alert
        v-if="preview.safety_status === 'blocked'"
        type="error"
        size="small"
        class="mt-3"
        :show-icon="false"
      >
        安全扫描判定为危险（blocked），导入未生效，请检查 SKILL.md 内容。
      </n-alert>
      <n-alert
        v-else-if="preview.skill_id"
        type="success"
        size="small"
        class="mt-3"
        :show-icon="false"
      >
        已成功导入到当前项目，可在列表中查看。
      </n-alert>
    </div>

    <template #action>
      <div class="flex justify-end gap-2">
        <n-button :disabled="busy" @click="visible = false">关闭</n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  NModal,
  NTabs,
  NTabPane,
  NButton,
  NInput,
  NForm,
  NFormItem,
  NTag,
  NAlert,
  NEmpty,
  useMessage,
} from "naive-ui";
import {
  importSkillUrlApi,
  importSkillZipApi,
  SKILL_ACTIVATION_LABEL,
  type SkillActivationMode,
  type SkillImportPreview,
} from "@/services/skills";
import SafetyBadge from "./SafetyBadge.vue";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{
  imported: [];
}>();
const visible = defineModel<boolean>("show", { default: false });

const message = useMessage();

const activeTab = ref<"zip" | "url" | "template">("zip");
const busy = ref(false);

const zipFile = ref<File | null>(null);
const dragOver = ref(false);
const pickerRef = ref<HTMLInputElement | null>(null);

const urlValue = ref("");
const urlRef = ref("");

const preview = ref<SkillImportPreview | null>(null);

watch(visible, (v) => {
  if (!v) {
    activeTab.value = "zip";
    zipFile.value = null;
    urlValue.value = "";
    urlRef.value = "";
    preview.value = null;
    busy.value = false;
  }
});

function activationLabel(mode: SkillActivationMode): string {
  return SKILL_ACTIVATION_LABEL[mode] ?? mode;
}

function triggerPicker() {
  pickerRef.value?.click();
}

function onPicker(ev: Event) {
  const target = ev.target as HTMLInputElement;
  zipFile.value = target.files?.[0] ?? null;
  if (target) target.value = "";
}

function handleDrop(ev: DragEvent) {
  dragOver.value = false;
  const file = ev.dataTransfer?.files?.[0];
  if (file) zipFile.value = file;
}

function resetZip() {
  zipFile.value = null;
  preview.value = null;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

async function submitZip() {
  if (!zipFile.value) return;
  busy.value = true;
  preview.value = null;
  try {
    const res = await importSkillZipApi(props.projectId, zipFile.value);
    if (res.success) {
      preview.value = res.data.preview;
      if (preview.value.safety_status === "blocked") {
        message.warning("安全扫描判定为高危，未导入，请检查 SKILL.md 内容");
      } else {
        message.success("已导入");
        emit("imported");
      }
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "导入失败";
    message.error(msg);
  } finally {
    busy.value = false;
  }
}

async function submitUrl() {
  if (!urlValue.value.trim()) return;
  busy.value = true;
  preview.value = null;
  try {
    const res = await importSkillUrlApi(props.projectId, urlValue.value.trim(), urlRef.value || undefined);
    if (res.success) {
      const detail = res.data;
      preview.value = {
        name: detail.name,
        slug: detail.slug,
        description: detail.description,
        semantic_version: detail.semantic_version,
        category: detail.category,
        activation_mode: detail.activation_mode,
        triggers: detail.triggers,
        tools_required: detail.tools_required,
        body_preview: detail.body.slice(0, 800),
        body_size_bytes: detail.body.length,
        attachments: detail.attachments,
        safety_status: detail.safety_scan_status,
        safety_findings: [],
        metadata_extra_keys: [],
        skill_id: detail.id,
      };
      message.success("已从 URL 导入");
      emit("imported");
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "URL 导入失败";
    message.error(msg);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.ski-dropzone {
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  padding: 24px;
  background: var(--bg-page-soft);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  transition: border-color var(--duration-fast) var(--easing-standard);
}

.ski-dropzone:hover,
.ski-dropzone--active {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.ski-dropzone--file {
  flex-direction: row;
  text-align: left;
  align-items: center;
}

.ski-dropzone__icon {
  font-size: 26px;
  color: var(--brand-primary);
}

.ski-dropzone__hint {
  font-size: 14px;
}

.ski-dropzone__sub {
  font-size: 12px;
  color: var(--text-tertiary);
}

.ski-dropzone__file {
  flex: 1;
}

.ski-dropzone__file-name {
  font-weight: 500;
}

.ski-input {
  display: none;
}

.ski-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.ski-url-form .n-form-item {
  margin-bottom: 12px;
}

.ski-empty {
  padding: 30px 0;
}

.ski-preview {
  margin-top: 16px;
  padding: 14px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
}

.ski-preview__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ski-preview__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
}

.ski-preview__slug {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  padding: 2px 6px;
  border-radius: 4px;
}

.ski-preview__desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 8px 0 12px;
  line-height: 1.55;
}

.ski-preview__row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  margin-bottom: 6px;
}

.ski-preview__label {
  width: 64px;
  color: var(--text-tertiary);
}

.ski-preview__tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.ski-findings {
  margin-top: 10px;
  font-size: 13px;
}

.ski-findings__title {
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.ski-findings ul {
  margin: 0;
  padding-left: 18px;
}

.ski-findings__snippet {
  color: var(--text-tertiary);
  margin-left: 8px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.text-muted {
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
