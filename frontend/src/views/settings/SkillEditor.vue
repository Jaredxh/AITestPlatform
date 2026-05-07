<template>
  <div class="ske">
    <page-header
      :title="pageTitle"
      :subtitle="isCreate ? '新建项目级技能（SKILL.md + 元数据）' : detail?.slug || '编辑技能'"
      icon="i-carbon-edit"
      back
      @back="goBack"
    >
      <template #extra>
        <n-space :size="8">
          <n-button :disabled="!form.body" @click="copyFullMd">
            <template #icon><span class="i-carbon-copy" /></template>
            复制 SKILL.md
          </n-button>
          <n-button v-if="canCopyAsCustom" @click="handleCopyAsCustom">
            <template #icon><span class="i-carbon-document" /></template>
            复制为自定义
          </n-button>
          <n-button :loading="saving" type="primary" :disabled="!projectId" @click="handleSave">
            <template #icon><span class="i-carbon-save" /></template>
            {{ isCreate ? "创建" : "保存" }}
          </n-button>
        </n-space>
      </template>
    </page-header>

    <n-alert v-if="isBuiltIn" type="warning" class="ske__alert" :show-icon="false">
      <span class="i-carbon-warning ske__alert-icon" />
      <strong>这是系统内置技能</strong>（<code>{{ detail?.slug }}</code>）。建议先「复制为自定义」再修改，避免后续平台升级被覆盖。
    </n-alert>

    <n-spin :show="loading">
      <!-- 上：元数据栅格 -->
      <page-container surface padded class="ske__meta">
        <n-form
          ref="formRef"
          :model="form"
          :rules="formRules"
          label-placement="top"
          :show-feedback="true"
        >
          <n-grid :cols="12" :x-gap="16" :y-gap="0" responsive="screen">
            <n-form-item-gi :span="7" label="名称" path="name">
              <n-input v-model:value="form.name" maxlength="200" placeholder="技能显示名" />
            </n-form-item-gi>
            <n-form-item-gi :span="2" label="版本" path="semantic_version">
              <n-input v-model:value="form.semantic_version" placeholder="1.0.0" />
            </n-form-item-gi>
            <n-form-item-gi :span="3" label="启用状态">
              <n-switch v-model:value="form.is_enabled">
                <template #checked>已启用</template>
                <template #unchecked>已禁用</template>
              </n-switch>
            </n-form-item-gi>

            <n-form-item-gi :span="7" label="slug（唯一）" path="slug">
              <n-input
                v-model:value="form.slug"
                :disabled="!isCreate"
                maxlength="100"
                placeholder="kebab-or-snake_case；不可包含 system_ 前缀"
              />
            </n-form-item-gi>
            <n-form-item-gi :span="2" label="分类" path="category">
              <n-input v-model:value="form.category" placeholder="custom" />
            </n-form-item-gi>
            <n-form-item-gi :span="3" label="激活模式" path="activation_mode">
              <n-select v-model:value="form.activation_mode" :options="activationOptions" />
            </n-form-item-gi>

            <n-form-item-gi :span="12" label="描述" path="description">
              <n-input
                v-model:value="form.description"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 3 }"
                placeholder="一句话说明此技能的用途与命中场景"
              />
            </n-form-item-gi>

            <n-form-item-gi :span="12" label="触发词">
              <trigger-editor v-model="form.triggers" />
            </n-form-item-gi>

            <n-form-item-gi :span="6" label="所需 platform 工具">
              <div class="ske__field-stack">
                <n-dynamic-tags v-model:value="form.tools_required" :max="20" />
                <span class="ske__hint">
                  仅 <code>system_*</code> 技能可暴露 platform_*；自定义技能即使声明也会被安全闸拦下。
                </span>
              </div>
            </n-form-item-gi>
            <n-form-item-gi :span="6" label="标签">
              <n-dynamic-tags v-model:value="form.tags" :max="20" />
            </n-form-item-gi>

            <n-form-item-gi v-if="form.attachments.length > 0" :span="12" label="附件">
              <div class="ske__attach">
                <div v-for="att in form.attachments" :key="att.path" class="ske__attach-row">
                  <span class="i-carbon-document ske__attach-icon" />
                  <code class="ske__attach-path">{{ att.path }}</code>
                  <span class="ske__attach-size">{{ formatSize(att.size) }}</span>
                </div>
              </div>
            </n-form-item-gi>

            <n-form-item-gi v-if="!isCreate" :span="12" label="变更说明">
              <n-input
                v-model:value="form.change_note"
                placeholder="本次保存的变更摘要（写入版本历史，可选）"
                maxlength="500"
              />
            </n-form-item-gi>
          </n-grid>
        </n-form>
      </page-container>

      <!-- 下：Markdown 编辑/预览 -->
      <page-container surface class="ske__md">
        <header class="ske__md-head">
          <div class="ske__md-title">
            <span class="i-carbon-document ske__md-title-icon" />
            <span>正文（SKILL.md）</span>
            <span class="ske__md-meta">{{ bodyLength }} 字符 · 约 {{ bodyLineCount }} 行</span>
          </div>
          <n-radio-group v-model:value="mdMode" size="small">
            <n-radio-button value="split">
              <span class="i-carbon-split-screen" /> 分屏
            </n-radio-button>
            <n-radio-button value="edit">
              <span class="i-carbon-edit" /> 仅编辑
            </n-radio-button>
            <n-radio-button value="preview">
              <span class="i-carbon-view" /> 仅预览
            </n-radio-button>
          </n-radio-group>
        </header>

        <div class="ske__md-body" :class="`ske__md-body--${mdMode}`">
          <div v-if="mdMode !== 'preview'" class="ske__md-pane">
            <textarea
              v-model="form.body"
              class="ske__md-textarea"
              spellcheck="false"
              placeholder="按 OpenClaw / Claude Code 风格编写 SKILL.md 正文（不含 YAML 前言，导出时自动拼接）"
            />
          </div>
          <div v-if="mdMode !== 'edit'" class="ske__md-pane">
            <div v-if="form.body" class="ske__md-preview" v-html="renderedHtml" />
            <div v-else class="ske__md-placeholder">
              <span class="i-carbon-view-off" />
              <span>暂无内容可预览</span>
            </div>
          </div>
        </div>
      </page-container>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NDynamicTags,
  NForm,
  NFormItemGi,
  NGrid,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";
import { marked } from "marked";
import DOMPurify from "dompurify";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import TriggerEditor from "@/components/skills/TriggerEditor.vue";
import { useProjectStore } from "@/stores/project";
import {
  createSkillApi,
  getSkillApi,
  SKILL_ACTIVATION_LABEL,
  updateSkillApi,
  type SkillActivationMode,
  type SkillAttachment,
  type SkillDetail,
} from "@/services/skills";

const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();
const message = useMessage();

const formRef = ref<FormInst | null>(null);
const projectId = computed(() => projectStore.currentProjectId || "");

const skillId = computed(() => {
  const raw = route.params.id;
  return Array.isArray(raw) ? raw[0] : raw;
});
const isCreate = computed(() => !skillId.value || skillId.value === "new");

const loading = ref(false);
const saving = ref(false);
const detail = ref<SkillDetail | null>(null);
const mdMode = ref<"split" | "edit" | "preview">("split");

const form = reactive({
  name: "",
  slug: "",
  description: "",
  semantic_version: "1.0.0",
  category: "custom",
  tags: [] as string[],
  triggers: [] as string[],
  tools_required: [] as string[],
  activation_mode: "agent_callable" as SkillActivationMode,
  body: "# 新技能\n\n## 何时使用\n\n描述触发场景。\n\n## 操作步骤\n\n1. ...\n",
  is_enabled: true,
  change_note: "",
  attachments: [] as SkillAttachment[],
});

const isBuiltIn = computed(() => detail.value?.source === "built_in");
const canCopyAsCustom = computed(() => !isCreate.value && isBuiltIn.value);

const pageTitle = computed(() => {
  if (isCreate.value) return "新建技能";
  return `编辑：${detail.value?.name ?? "技能"}`;
});

const activationOptions = (
  Object.keys(SKILL_ACTIVATION_LABEL) as SkillActivationMode[]
).map((m) => ({ label: SKILL_ACTIVATION_LABEL[m], value: m }));

const formRules: FormRules = {
  name: [{ required: true, message: "请输入名称", trigger: "blur" }],
  slug: [
    { required: true, message: "请输入 slug", trigger: "blur" },
    {
      pattern: /^[a-z0-9]+(?:[_-][a-z0-9]+)*$/,
      message: "仅小写字母、数字与单个 _/- 分隔",
      trigger: "blur",
    },
    {
      validator(_rule, v) {
        if (typeof v === "string" && v.startsWith("system_")) {
          return new Error("system_* 命名空间保留给内置技能");
        }
        return true;
      },
      trigger: "blur",
    },
  ],
  description: [{ required: true, message: "请输入描述", trigger: "blur" }],
};

const renderedHtml = computed(() => {
  const html = marked.parse(form.body || "", { async: false }) as string;
  return DOMPurify.sanitize(html);
});

const bodyLength = computed(() => form.body.length);
const bodyLineCount = computed(() => form.body.split("\n").length);

function fullSkillMd(): string {
  const yaml = [
    `name: ${form.name}`,
    `slug: ${form.slug}`,
    `description: ${form.description.replace(/\n/g, " ")}`,
    `version: ${form.semantic_version}`,
    `category: ${form.category}`,
    `tags: [${form.tags.map((t) => JSON.stringify(t)).join(", ")}]`,
    `triggers: [${form.triggers.map((t) => JSON.stringify(t)).join(", ")}]`,
    `tools_required: [${form.tools_required.map((t) => JSON.stringify(t)).join(", ")}]`,
    `activation_mode: ${form.activation_mode}`,
  ].join("\n");
  const body = (form.body || "").replace(/^\n+/, "");
  return `---\n${yaml}\n---\n\n${body}`;
}

async function copyFullMd() {
  try {
    await navigator.clipboard.writeText(fullSkillMd());
    message.success("完整 SKILL.md 已复制到剪贴板");
  } catch {
    message.error("复制失败，请手动选中复制");
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function applyDetail(d: SkillDetail) {
  detail.value = d;
  form.name = d.name;
  form.slug = d.slug;
  form.description = d.description;
  form.semantic_version = d.semantic_version;
  form.category = d.category;
  form.tags = [...(d.tags || [])];
  form.triggers = [...(d.triggers || [])];
  form.tools_required = [...(d.tools_required || [])];
  form.activation_mode = d.activation_mode;
  form.body = d.body;
  form.is_enabled = d.is_enabled;
  form.attachments = [...(d.attachments || [])];
  form.change_note = "";
}

async function loadDetail() {
  if (isCreate.value || !skillId.value) return;
  loading.value = true;
  try {
    const res = await getSkillApi(skillId.value);
    if (res.success) applyDetail(res.data);
  } catch {
    message.error("加载技能详情失败");
  } finally {
    loading.value = false;
  }
}

function handleCopyAsCustom() {
  if (!detail.value) return;
  const base = detail.value;
  const slugSeed = base.slug.replace(/^system_/, "");
  router.push({ name: "SkillEditor", params: { id: "new" } }).then(() => {
    detail.value = null;
    form.name = `${base.name}（副本）`;
    form.slug = `${slugSeed}-copy`;
    form.description = base.description;
    form.semantic_version = "1.0.0";
    form.category = base.category === "system" ? "custom" : base.category;
    form.tags = [...(base.tags || [])];
    form.triggers = [...(base.triggers || [])];
    form.tools_required = [];
    form.activation_mode = base.activation_mode === "always" ? "manual" : base.activation_mode;
    form.body = base.body;
    form.is_enabled = true;
    form.attachments = [];
    form.change_note = "";
    message.info("已复制为草稿，请改 slug 后再保存");
  });
}

async function handleSave() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  if (!form.body.trim()) {
    message.error("请输入正文");
    mdMode.value = mdMode.value === "preview" ? "split" : mdMode.value;
    return;
  }
  if (!projectId.value) {
    message.error("未选择项目");
    return;
  }
  saving.value = true;
  try {
    if (isCreate.value) {
      const res = await createSkillApi(projectId.value, {
        name: form.name,
        slug: form.slug,
        description: form.description,
        semantic_version: form.semantic_version,
        category: form.category,
        tags: form.tags,
        triggers: form.triggers,
        tools_required: form.tools_required,
        activation_mode: form.activation_mode,
        body: form.body,
      });
      if (res.success) {
        message.success("技能已创建");
        router.replace({ name: "SkillEditor", params: { id: res.data.id } });
        applyDetail(res.data);
      }
    } else if (skillId.value) {
      const res = await updateSkillApi(skillId.value, {
        name: form.name,
        description: form.description,
        semantic_version: form.semantic_version,
        category: form.category,
        tags: form.tags,
        triggers: form.triggers,
        tools_required: form.tools_required,
        activation_mode: form.activation_mode,
        body: form.body,
        is_enabled: form.is_enabled,
        change_note: form.change_note || undefined,
      });
      if (res.success) {
        message.success(`已保存 (db v${res.data.db_version})`);
        applyDetail(res.data);
      }
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

function goBack() {
  router.push({ name: "SkillManagement" });
}

watch(() => skillId.value, loadDetail);
onMounted(loadDetail);
</script>

<style scoped>
/* 关键：根容器要明确"不超过父容器宽度且禁止内部撑开主框架"。
 * 历史 bug：textarea / 长 slug / n-grid 内的某些 Naive 子组件（dynamic-tags 等）
 * 默认 ``min-width: auto`` 会让父 grid track 被内容撑大，超出 .app-content
 * 的可视区，主页面就出现整体横向滚动条，新建/编辑技能页"比框架还宽"。
 * - ``min-width: 0`` ：允许在 flex/grid 父容器中收缩到 0 而不是 auto；
 * - ``max-width: 100%`` + ``box-sizing: border-box``：兜底防 padding 外撑。
 */
.ske {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}

.ske__alert {
  margin: 0 0 14px;
}

.ske__alert-icon {
  margin-right: 6px;
  vertical-align: -2px;
}

.ske__meta {
  margin-bottom: 16px;
}

.ske__field-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}

.ske__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.ske__attach {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 6px;
  width: 100%;
}

.ske__attach-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  background: var(--bg-page-soft);
  font-size: 12px;
}

.ske__attach-icon {
  font-size: 14px;
  color: var(--text-tertiary);
}

.ske__attach-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text-secondary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ske__attach-size {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

/* Markdown 区 */
.ske__md {
  display: flex;
  flex-direction: column;
}

.ske__md-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-page-soft);
}

.ske__md-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.ske__md-title-icon {
  font-size: 16px;
  color: var(--brand-primary);
}

.ske__md-meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 8px;
}

.ske__md-body {
  display: grid;
  min-height: 460px;
}

/* ``minmax(0, 1fr)`` 而不是 ``1fr``：1fr 默认 min-width = auto，
 * textarea 里出现极长一行/不可断 URL 时会把列硬撑大，连带主框架横滚。
 * 用 minmax(0, 1fr) 可以严格限制最大列宽 = 容器宽 / 列数。 */
.ske__md-body--split {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}

.ske__md-body--edit,
.ske__md-body--preview {
  grid-template-columns: minmax(0, 1fr);
}

.ske__md-pane {
  min-width: 0;
  min-height: 460px;
  overflow: auto;
}

.ske__md-pane + .ske__md-pane {
  border-left: 1px solid var(--border-subtle);
}

.ske__md-textarea {
  /* box-sizing 关键：默认 content-box 会让 ``width:100% + padding 14/16`` 总宽
   * = 100% + 32px，把父 ``.ske__md-pane`` 撑出主框架；border-box 严格限定
   * 元素总宽 = 100%。 */
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  height: 100%;
  min-height: 460px;
  border: none;
  outline: none;
  resize: none;
  padding: 14px 16px;
  background: var(--bg-card);
  color: var(--text-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.65;
  tab-size: 2;
  /* 长 URL / 长行 wrap 而不是横滚；视觉一致且不撑容器 */
  white-space: pre-wrap;
  word-break: break-word;
}

.ske__md-textarea:focus {
  background: var(--bg-card);
}

.ske__md-preview {
  font-size: 13px;
  line-height: 1.7;
  padding: 14px 18px;
  color: var(--text-primary);
}

.ske__md-preview :deep(h1),
.ske__md-preview :deep(h2),
.ske__md-preview :deep(h3) {
  margin: 14px 0 8px;
  font-weight: 600;
}

.ske__md-preview :deep(h1) {
  font-size: 18px;
}
.ske__md-preview :deep(h2) {
  font-size: 16px;
}
.ske__md-preview :deep(h3) {
  font-size: 14px;
}

.ske__md-preview :deep(p) {
  margin: 8px 0;
}

.ske__md-preview :deep(code) {
  background: var(--bg-page-soft);
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.ske__md-preview :deep(pre) {
  background: var(--bg-page-soft);
  padding: 12px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  font-size: 12px;
}

.ske__md-preview :deep(ul),
.ske__md-preview :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.ske__md-preview :deep(blockquote) {
  border-left: 3px solid var(--brand-primary);
  padding: 4px 12px;
  margin: 8px 0;
  background: var(--bg-page-soft);
  color: var(--text-secondary);
}

.ske__md-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 460px;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.ske__md-placeholder span:first-child {
  font-size: 32px;
}

@media (max-width: 1100px) {
  .ske__md-body--split {
    grid-template-columns: 1fr;
  }
  .ske__md-pane + .ske__md-pane {
    border-left: none;
    border-top: 1px solid var(--border-subtle);
  }
}
</style>
