<template>
  <n-modal
    :show="show"
    preset="card"
    :mask-closable="false"
    :title="title"
    :style="{ width: '880px', maxWidth: 'calc(100vw - 32px)' }"
    class="env-wizard"
    @update:show="$emit('update:show', $event)"
  >
    <n-spin :show="loadingInitial">
      <n-tabs
        v-model:value="activeTab"
        type="line"
        animated
        size="medium"
        pane-class="env-wizard__pane"
      >
        <!-- ── 基本信息 ────────────────────────────────────────── -->
        <n-tab-pane name="basic" tab="基本信息">
          <n-form
            ref="basicFormRef"
            :model="formData"
            :rules="basicRules"
            label-placement="top"
          >
            <n-form-item label="环境名称" path="name" required>
              <n-input
                v-model:value="formData.name"
                placeholder="如：staging / pre-release / 本地开发"
                maxlength="100"
                show-count
              />
            </n-form-item>

            <n-form-item label="环境描述">
              <n-input
                v-model:value="formData.description"
                type="textarea"
                placeholder="例：接入测试账号 admin@demo.com / pwd 123456，可执行写操作"
                :rows="2"
              />
            </n-form-item>

            <n-form-item path="base_url" required>
              <template #label>
                <span>
                  系统根 URL
                  <n-tooltip>
                    <template #trigger>
                      <span class="i-carbon-information-square ml-1 inline-block align-middle" />
                    </template>
                    被测系统的<strong>主域</strong>，例如 <code>https://app.example.com</code>。
                    它在系统里有三件事：<br />
                    1. 自动推导域名白名单（决定 AI 能 navigate 到哪）<br />
                    2. 与登录前置一起决定登录态归属<br />
                    3. 与"用例模块入口路径"拼接成实际目标 URL：
                    <code>base_url + module.entry_path</code><br />
                    每个被测系统建一个环境即可；不同子模块的入口请到测试用例页配。
                  </n-tooltip>
                </span>
              </template>
              <n-input
                v-model:value="formData.base_url"
                placeholder="https://app.example.com"
              />
            </n-form-item>

            <n-form-item label="Session 名称（可选）">
              <n-input
                v-model:value="formData.session_name"
                placeholder="留空 = 每个环境独立保存登录态；填同名 = 多个环境共享登录态"
                maxlength="100"
              />
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- ── 浏览器 & 安全 ───────────────────────────────────── -->
        <n-tab-pane name="browser" tab="浏览器 &amp; 安全">
          <n-form :model="formData" label-placement="top">
            <n-form-item>
              <template #label>
                <span>
                  允许的域名白名单
                  <n-tooltip>
                    <template #trigger>
                      <span class="i-carbon-information-square ml-1 inline-block align-middle" />
                    </template>
                    只有命中白名单的 URL 才允许 browser_navigate；防 prompt 注入诱导去攻击者站点。创建时会自动从 Base URL 取 host。
                  </n-tooltip>
                </span>
              </template>
              <n-dynamic-tags v-model:value="formData.allowed_hosts" />
              <template #feedback>
                支持精确匹配（<code>staging.foo.com</code>）、端口（<code>localhost:8080</code>）、通配符子域（<code>*.foo.com</code>）。需要完全开放可填一条 <code>*</code>（关闭 host 校验，慎用）。
              </template>
            </n-form-item>

            <n-grid :cols="2" :x-gap="16">
              <n-gi>
                <n-form-item label="Token 预算（单次执行上限）">
                  <n-input-number
                    v-model:value="formData.token_budget"
                    :min="1000"
                    :max="10_000_000"
                    :step="5000"
                    class="w-full"
                  />
                </n-form-item>
              </n-gi>
              <n-gi>
                <n-form-item label="运行模式">
                  <n-select
                    v-model:value="headlessValue"
                    :options="headlessOptions"
                  />
                </n-form-item>
              </n-gi>
            </n-grid>

            <n-grid :cols="2" :x-gap="16">
              <n-gi>
                <n-form-item label="视口宽度">
                  <n-input-number
                    v-model:value="formData.viewport_width"
                    :min="320"
                    :max="4096"
                    :step="16"
                    class="w-full"
                  />
                </n-form-item>
              </n-gi>
              <n-gi>
                <n-form-item label="视口高度">
                  <n-input-number
                    v-model:value="formData.viewport_height"
                    :min="240"
                    :max="4096"
                    :step="16"
                    class="w-full"
                  />
                </n-form-item>
              </n-gi>
            </n-grid>

            <n-form-item>
              <n-checkbox v-model:checked="formData.enable_browser_evaluate">
                允许 AI 执行任意 JavaScript
                <span class="env-wizard__hint"
                  >（默认禁用；开启前请确认执行目标为测试环境）</span
                >
              </n-checkbox>
            </n-form-item>
          </n-form>
        </n-tab-pane>

        <!-- ── 默认物料集 ───────────────────────────────────────── -->
        <n-tab-pane name="data-sets" tab="默认物料集">
          <n-form :model="formData" label-placement="top">
            <n-form-item>
              <template #label>
                <span>
                  环境级默认物料集
                  <n-tooltip>
                    <template #trigger>
                      <span
                        class="i-carbon-information-square ml-1 inline-block align-middle"
                      />
                    </template>
                    在该环境下执行任何用例时自动加载的物料集。与项目级 / 用例级 / 个人级 / 执行级合并；顺序越靠前优先级越低（后覆盖前）。Task 9.1 消费。
                  </n-tooltip>
                </span>
              </template>
              <n-alert
                v-if="!props.projectId"
                type="warning"
                :bordered="false"
                size="small"
              >
                请先保存基本信息再来绑定物料集
              </n-alert>
              <set-selector
                v-else
                v-model="formData.default_data_set_ids"
                :project-id="props.projectId"
                :allowed-scopes="['project', 'environment']"
                @click-set="handleViewSetDetail"
              />
            </n-form-item>
            <n-alert type="info" :bordered="false" size="small">
              建议只在环境 tab 绑定"项目级"和"环境级"物料集，个人物料集应在「测试物料」页签下维护并由用户在执行弹窗中选择。
              <span class="text-tertiary">点击已选物料集 chip 可在下方查看明细。</span>
            </n-alert>

            <!-- 物料集明细：用户点击已添加的物料集 chip 后展开。
                 修复点：原先用户从 "添加物料集" 选完后，无法看到该物料集到底
                 包含哪些 key/value，必须跑去「测试物料」页面才能查；现在加内
                 联明细面板，编辑环境时即可确认配置无误（2026-05 验收反馈）。 -->
            <n-card
              v-if="selectedSetDetail || loadingSetDetail"
              size="small"
              class="env-wizard__set-detail"
            >
              <template #header>
                <div class="env-wizard__set-detail-head">
                  <span class="i-carbon-data-table" />
                  <strong>{{ selectedSetDetail?.name ?? "加载中…" }}</strong>
                  <n-tag
                    v-if="selectedSetDetail"
                    size="tiny"
                    :type="(SCOPE_META[selectedSetDetail.scope as DataSetScope].color as 'default' | 'primary' | 'info' | 'success' | 'warning' | 'error')"
                    :bordered="false"
                  >
                    {{ SCOPE_META[selectedSetDetail.scope as DataSetScope].label }}
                  </n-tag>
                  <span v-if="selectedSetDetailItems.length > 0" class="text-xs text-tertiary">
                    共 {{ selectedSetDetailItems.length }} 项
                  </span>
                </div>
              </template>
              <template #header-extra>
                <n-button text size="tiny" @click="closeSetDetail">
                  <template #icon><span class="i-carbon-close" /></template>
                </n-button>
              </template>
              <n-spin :show="loadingSetDetail">
                <n-empty
                  v-if="!loadingSetDetail && selectedSetDetailItems.length === 0"
                  size="small"
                  description="该物料集暂无明细条目"
                />
                <n-data-table
                  v-else
                  :columns="setDetailColumns"
                  :data="selectedSetDetailItems"
                  :bordered="false"
                  size="small"
                  :scroll-x="500"
                  striped
                  :max-height="280"
                />
              </n-spin>
            </n-card>
          </n-form>
        </n-tab-pane>

        <!-- ── 前置步骤（仅编辑态显示）──────────────────────────── -->
        <n-tab-pane
          v-if="isEditMode"
          name="preconditions"
          :tab="preconditionTabLabel"
        >
          <precondition-editor
            :environment-id="environmentId!"
            :preconditions="preconditions"
            @changed="reloadPreconditions"
          />
        </n-tab-pane>
        <n-tab-pane v-else name="preconditions_hint" tab="前置步骤" disabled>
          <n-alert type="info">
            先保存"基本信息"和"浏览器 &amp; 安全"两个 tab 创建环境，再来这里添加前置步骤（state 注入 / AI 登录 / 脚本步骤 / Cookie 注入）。
          </n-alert>
        </n-tab-pane>
      </n-tabs>
    </n-spin>

    <template #footer>
      <div class="env-wizard__footer">
        <n-button @click="handleCancel">{{ isEditMode ? "关闭" : "取消" }}</n-button>
        <n-button
          type="primary"
          :loading="submitting"
          @click="handleSubmit"
        >
          {{ isEditMode ? "保存修改" : "创建环境" }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, computed, h, reactive, watch, nextTick } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NDynamicTags,
  NEmpty,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, FormInst, FormRules } from "naive-ui";

import {
  createEnvironmentApi,
  getEnvironmentApi,
  updateEnvironmentApi,
} from "@/services/uiAutomation";
import type {
  EnvironmentCreateParams,
  EnvironmentUpdateParams,
  PreconditionTemplate,
  TestEnvironmentDetail,
} from "@/services/uiAutomation";
import {
  getSetApi,
  listItemsApi,
  SCOPE_META,
} from "@/services/testData";
import type {
  DataSetScope,
  TestDataItem,
  TestDataSet,
  TestDataSetDetail,
} from "@/services/testData";
import PreconditionEditor from "./PreconditionEditor.vue";
import SetSelector from "@/components/test-data/SetSelector.vue";

const props = defineProps<{
  show: boolean;
  projectId: string;
  /** null = 创建模式；string = 编辑模式 */
  environmentId: string | null;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
  saved: [envId: string];
}>();

const message = useMessage();

const isEditMode = computed(() => !!props.environmentId);
const title = computed(() =>
  isEditMode.value ? "编辑 UI 自动化环境" : "新建 UI 自动化环境",
);

const activeTab = ref<string>("basic");
const loadingInitial = ref(false);
const submitting = ref(false);
const basicFormRef = ref<FormInst | null>(null);

const preconditions = ref<PreconditionTemplate[]>([]);

// 物料集明细面板状态：用户在 SetSelector 里点击某个 chip 时载入。
// 不缓存（每次点重新拉），保证显示数据最新；条目通常 < 50 条，对网络无压力。
const selectedSetDetail = ref<TestDataSet | TestDataSetDetail | null>(null);
const selectedSetDetailItems = ref<TestDataItem[]>([]);
const loadingSetDetail = ref(false);

async function handleViewSetDetail(set: TestDataSet) {
  // 已经在显示同一个 set 时再点 = 收起
  if (selectedSetDetail.value && selectedSetDetail.value.id === set.id) {
    closeSetDetail();
    return;
  }
  selectedSetDetail.value = set;
  selectedSetDetailItems.value = [];
  loadingSetDetail.value = true;
  try {
    // 并行拉两个：set detail（拿 owner / category 等元信息）+ items 列表
    const [detailRes, itemsRes] = await Promise.all([
      getSetApi(set.id),
      listItemsApi(set.id),
    ]);
    if (detailRes.success) selectedSetDetail.value = detailRes.data;
    if (itemsRes.success) selectedSetDetailItems.value = itemsRes.data.items;
  } catch (err) {
    message.error(err instanceof Error ? err.message : "加载物料明细失败");
  } finally {
    loadingSetDetail.value = false;
  }
}

function closeSetDetail() {
  selectedSetDetail.value = null;
  selectedSetDetailItems.value = [];
}

const VALUE_TYPE_LABEL: Record<string, string> = {
  string: "文本",
  multiline: "多行",
  secret: "凭据",
  file: "文件",
  random: "随机",
  dataset: "数据组",
};

/** 物料明细列：紧凑布局 + 横滚兜底，避免在 880px modal 里再次撑爆。
 *  secret 列绝不展示明文（只显示 ●●●●），防止环境编辑场景泄露凭据。 */
const setDetailColumns: DataTableColumns<TestDataItem> = [
  {
    title: "Key",
    key: "key",
    width: 140,
    ellipsis: { tooltip: true },
    render: (row) =>
      h("code", { class: "env-wizard__detail-key" }, row.key),
  },
  {
    title: "类型",
    key: "value_type",
    width: 70,
    render: (row) =>
      h(
        NTag,
        { size: "tiny", bordered: false },
        () => VALUE_TYPE_LABEL[row.value_type] ?? row.value_type,
      ),
  },
  {
    title: "值",
    key: "value_text",
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) => {
      if (row.has_secret_value) {
        return h(
          "span",
          { class: "env-wizard__detail-secret" },
          "●●●●（凭据，已加密）",
        );
      }
      if (row.value_type === "file" && row.file_path) {
        return h(
          "span",
          { class: "env-wizard__detail-file" },
          [
            "📎 ",
            row.file_path.split("/").pop() ?? row.file_path,
          ],
        );
      }
      const v = row.value_text ?? (row.value_json != null
        ? JSON.stringify(row.value_json)
        : "");
      return v || h("span", { class: "text-tertiary" }, "—");
    },
  },
  {
    title: "说明",
    key: "description",
    minWidth: 110,
    ellipsis: { tooltip: true },
    render: (row) => row.description || "—",
  },
];

// 表单默认值：同步 backend schemas 的默认
const defaults = () => ({
  name: "",
  description: "",
  base_url: "",
  session_name: "",
  allowed_hosts: [] as string[],
  token_budget: 25_000,
  enable_browser_evaluate: false,
  headless: true,
  viewport_width: 1280,
  viewport_height: 800,
  default_data_set_ids: [] as string[],
});

const formData = reactive(defaults());

// 由于 naive-ui n-select 需要 string 值而 boolean 易读性更好，做一层映射
const headlessValue = computed<"headless" | "headed">({
  get: () => (formData.headless ? "headless" : "headed"),
  set: (v) => {
    formData.headless = v === "headless";
  },
});
const headlessOptions = [
  { label: "Headless（容器 / CI 推荐）", value: "headless" },
  { label: "Headed（可见浏览器，本地调试用）", value: "headed" },
];

const basicRules: FormRules = {
  name: [{ required: true, message: "请输入环境名称", trigger: "blur" }],
  base_url: [
    { required: true, message: "请输入 Base URL", trigger: "blur" },
    {
      validator: (_rule, value: string) => {
        if (!value) return true;
        try {
          const u = new URL(value);
          if (!/^https?:$/.test(u.protocol)) {
            return new Error("仅支持 http/https");
          }
        } catch {
          return new Error("URL 格式不正确");
        }
        return true;
      },
      trigger: "blur",
    },
  ],
};

const preconditionTabLabel = computed(() => {
  const enabled = preconditions.value.filter((p) => p.enabled).length;
  const total = preconditions.value.length;
  if (total === 0) return "前置步骤";
  return `前置步骤 · ${enabled}/${total}`;
});

// ─── 初始化 / 重置 ───────────────────────────────────────────────────

function resetForm() {
  Object.assign(formData, defaults());
  preconditions.value = [];
  activeTab.value = "basic";
}

async function loadEnvironment(envId: string) {
  loadingInitial.value = true;
  try {
    const res = await getEnvironmentApi(envId);
    if (res.success) {
      const env: TestEnvironmentDetail = res.data;
      formData.name = env.name;
      formData.description = env.description ?? "";
      formData.base_url = env.base_url;
      formData.session_name = env.session_name ?? "";
      formData.allowed_hosts = [...env.allowed_hosts];
      formData.token_budget = env.token_budget;
      formData.enable_browser_evaluate = env.enable_browser_evaluate;
      formData.headless = env.headless;
      formData.viewport_width = env.viewport_width;
      formData.viewport_height = env.viewport_height;
      formData.default_data_set_ids = [...(env.default_data_set_ids ?? [])];
      preconditions.value = env.preconditions ?? [];
    }
  } catch {
    message.error("加载环境详情失败");
  } finally {
    loadingInitial.value = false;
  }
}

async function reloadPreconditions() {
  if (!props.environmentId) return;
  const res = await getEnvironmentApi(props.environmentId);
  if (res.success) {
    preconditions.value = res.data.preconditions ?? [];
  }
}

// ─── 打开 / 关闭时重置 ───────────────────────────────────────────────

watch(
  () => props.show,
  async (val) => {
    if (!val) return;
    resetForm();
    await nextTick();
    if (props.environmentId) {
      await loadEnvironment(props.environmentId);
    }
  },
);

// ─── 提交 ────────────────────────────────────────────────────────────

function buildPayload(): EnvironmentCreateParams | EnvironmentUpdateParams {
  return {
    name: formData.name.trim(),
    description: formData.description.trim() || undefined,
    base_url: formData.base_url.trim(),
    allowed_hosts: formData.allowed_hosts,
    token_budget: formData.token_budget,
    enable_browser_evaluate: formData.enable_browser_evaluate,
    session_name: formData.session_name.trim() || null,
    headless: formData.headless,
    viewport_width: formData.viewport_width,
    viewport_height: formData.viewport_height,
    default_data_set_ids: [...formData.default_data_set_ids],
  };
}

async function handleSubmit() {
  // 切到基本信息 tab 再做表单校验；tabs 隐藏时 NForm 找不到对应 ref
  if (activeTab.value !== "basic") {
    activeTab.value = "basic";
    await nextTick();
  }
  try {
    await basicFormRef.value?.validate();
  } catch {
    message.warning("请检查「基本信息」中的必填项");
    return;
  }

  submitting.value = true;
  try {
    if (isEditMode.value && props.environmentId) {
      const res = await updateEnvironmentApi(props.environmentId, buildPayload());
      if (res.success) {
        message.success("环境已更新");
        emit("saved", res.data.id);
        emit("update:show", false);
      }
    } else {
      const res = await createEnvironmentApi(
        props.projectId,
        buildPayload() as EnvironmentCreateParams,
      );
      if (res.success) {
        message.success("环境已创建，可在「前置步骤」tab 继续添加登录流程");
        emit("saved", res.data.id);
        emit("update:show", false);
      }
    }
  } catch (err: unknown) {
    const msg = extractErrorMessage(err) ?? "保存失败";
    message.error(msg);
  } finally {
    submitting.value = false;
  }
}

function handleCancel() {
  emit("update:show", false);
}

function extractErrorMessage(err: unknown): string | null {
  if (err && typeof err === "object" && "data" in err) {
    const data = (err as { data?: { message?: string } }).data;
    if (data?.message) return data.message;
  }
  if (err instanceof Error) return err.message;
  return null;
}
</script>

<style scoped>
.env-wizard__pane {
  min-height: 360px;
  padding-top: 8px !important;
}

.env-wizard__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.env-wizard__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

/* 物料明细面板：与 SetSelector 之间留点空隙；轻边框淡背景与表单区分。 */
.env-wizard__set-detail {
  margin-top: 12px;
  background: var(--bg-page-soft, var(--bg-card));
  border: 1px solid var(--border-subtle);
}

.env-wizard__set-detail-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.env-wizard__detail-key {
  display: inline-block;
  padding: 1px 6px;
  background: var(--bg-page-soft);
  color: var(--text-secondary);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.env-wizard__detail-secret {
  color: var(--color-warning, #b45309);
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  letter-spacing: 1px;
}

.env-wizard__detail-file {
  color: var(--brand-primary);
  font-size: 12.5px;
}

.text-tertiary {
  color: var(--text-tertiary);
}

.text-xs {
  font-size: 12px;
}
</style>
