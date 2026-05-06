<template>
  <!-- 单弹窗折叠式执行配置（Task 10.1 / PHASE2_DESIGN §2.5.1）。
       设计哲学（v3.0.1）：
       - 默认状态只露顶部摘要 + 两个折叠区 + 可能的缺料 banner + 立即执行按钮
       - "零必填"——除环境（默认填好）外全部可选；用户什么都不调也能一键跑
       - 缺料只警告不阻断（除非勾了严格模式）；AI 用 platform_synthesize_data
         兜底，结果按 data_confidence 自动分级
       - "↩ 复用上次"调 GET /recent-executions/last-config，把上次的配置一键回填 -->
  <n-modal
    v-model:show="visible"
    preset="card"
    :title="title"
    :style="{ width: '880px', maxWidth: 'calc(100vw - 32px)' }"
    :mask-closable="!submitting"
    :closable="!submitting"
    display-directive="show"
  >
    <n-spin :show="loadingInitial">
      <!-- 环境空态指引：避免下拉默默为空让用户莫名（v3.0.2）。
           bootstrap 完成后才显示，否则首屏 0.x 秒会闪一下。 -->
      <n-alert
        v-if="!loadingInitial && !loadingEnvs && environments.length === 0"
        type="warning"
        :show-icon="true"
        class="mb-3"
      >
        <template #header>当前项目还没有 UI 执行环境</template>
        <div class="flex items-center justify-between gap-3 flex-wrap">
          <span class="text-sm">
            执行 UI 测试需要先配置一个目标环境（base_url / 浏览器参数等）。请前往「环境管理」创建。
          </span>
          <n-button size="small" type="primary" ghost @click="goCreateEnvironment">
            <template #icon><span class="i-carbon-add" /></template>
            前往创建环境
          </n-button>
        </div>
      </n-alert>

      <!-- ── 顶部摘要行：用例数 / 环境 / state / 预算 ─────────── -->
      <div class="exec-dialog__header-row">
        <div class="exec-dialog__count">
          <span class="i-carbon-task" />
          <strong>{{ testcaseIds.length }}</strong>
          <span>条用例</span>
        </div>

        <n-select
          :value="environmentId"
          :options="environmentOptions"
          :loading="loadingEnvs"
          :placeholder="environmentPlaceholder"
          :disabled="environments.length === 0"
          :consistent-menu-width="false"
          size="small"
          class="exec-dialog__env"
          @update:value="handleEnvironmentChange"
        />

        <div v-if="selectedEnvHealth" class="exec-dialog__state">
          <span :class="stateIconFor(selectedEnvHealth.kind)" />
          <span class="text-xs">{{ selectedEnvHealth.label }}</span>
        </div>

        <div class="exec-dialog__budget">
          <span class="i-carbon-meter-alt text-tertiary" />
          <span class="text-xs">{{ budgetSummary }}</span>
        </div>

        <n-button
          v-if="canReuseRecent"
          quaternary
          size="tiny"
          class="exec-dialog__reuse"
          @click="reuseRecentConfig"
        >
          <template #icon><span class="i-carbon-renew" /></template>
          复用上次
        </n-button>
      </div>

      <!-- ── 两个 accordion ─────────────────────────────────── -->
      <n-collapse
        :default-expanded-names="[]"
        accordion
        class="exec-dialog__collapse"
      >
        <!-- 物料折叠区 -->
        <n-collapse-item name="material">
          <template #header>
            <div class="exec-dialog__section-header">
              <span class="i-carbon-data-base" />
              <span>测试物料</span>
              <span class="text-xs text-tertiary">
                已自动加载 {{ mergedItems.length }} 项
                <template v-if="missingKeys.length > 0">
                  · <span class="text-warning">{{ missingKeys.length }} 项缺料</span>
                </template>
              </span>
            </div>
          </template>

          <div class="exec-dialog__material">
            <div class="exec-dialog__material-block">
              <div class="exec-dialog__block-title">推荐物料集</div>
              <data-recommendation
                :recommendations="recommendations"
                :selected="loadedSetIds"
                :loading="loadingRecommendations"
                @update:selected="handleSelectedSetsChange"
              />
            </div>

            <div class="exec-dialog__material-block">
              <div class="exec-dialog__block-title">合并预览</div>
              <data-merge-preview
                :items="mergedItems"
                :manual-overrides="manualOverrides"
                :loading="loadingPreview"
                @update:manual-overrides="handleOverridesChange"
              />
            </div>

            <div class="exec-dialog__material-foot">
              <n-text depth="3" class="text-xs">
                改动只对本次执行生效；想沉淀为长期物料请用「测试物料」页面。
              </n-text>
            </div>
          </div>
        </n-collapse-item>

        <!-- 测试地址折叠区（按模块） -->
        <n-collapse-item
          v-if="preflightModules.length > 0"
          name="targets"
        >
          <template #header>
            <div class="exec-dialog__section-header">
              <span class="i-carbon-launch" />
              <span>测试地址</span>
              <span class="text-xs text-tertiary">{{ targetsSummary }}</span>
            </div>
          </template>

          <n-alert
            v-if="!selectedEnv"
            type="info"
            :show-icon="false"
            size="small"
            class="mb-2"
          >
            选择环境后，相对路径（如 <code>/admin/users</code>）会拼接到环境的 base_url 上。
          </n-alert>
          <div class="exec-dialog__module-list">
            <div
              v-for="m in preflightModules"
              :key="m.module_id ?? '__no_module__'"
              class="exec-dialog__module-row"
            >
              <div class="exec-dialog__module-meta">
                <div class="exec-dialog__module-name">
                  {{ m.module_name ?? "（未归模块）" }}
                  <span class="text-xs text-tertiary ml-1">
                    · {{ m.case_count }} 条用例
                  </span>
                </div>
                <div
                  v-if="m.module_id && resolvedTargetUrl(m)"
                  class="exec-dialog__module-resolved"
                  :title="resolvedTargetUrl(m) ?? ''"
                >
                  目标 URL：<code>{{ resolvedTargetUrl(m) }}</code>
                </div>
                <div
                  v-else-if="m.module_id"
                  class="exec-dialog__module-resolved exec-dialog__module-resolved--empty"
                >
                  未配置入口路径，AI 将依据用例步骤自然语言决定目标地址
                </div>
                <div v-else class="exec-dialog__module-resolved exec-dialog__module-resolved--empty">
                  未归模块的用例无法配置入口路径，跑时由用例步骤自然语言驱动
                </div>
              </div>
              <div v-if="m.module_id" class="exec-dialog__module-input">
                <n-input
                  :value="entryInputValue(m)"
                  size="small"
                  placeholder="例如：/admin/users 或 https://other.example.com/x"
                  :maxlength="500"
                  clearable
                  @update:value="(v: string) => updateEntryOverride(m, v)"
                />
              </div>
            </div>
          </div>
          <div class="exec-dialog__module-foot">
            <n-text depth="3" class="text-xs">
              在这里改的入口路径只对本次执行生效；想长期记住，请去测试用例页面对应模块编辑。
            </n-text>
          </div>
        </n-collapse-item>

        <!-- 高级折叠区 -->
        <n-collapse-item name="advanced">
          <template #header>
            <div class="exec-dialog__section-header">
              <span class="i-carbon-settings" />
              <span>高级</span>
              <span class="text-xs text-tertiary">{{ advancedSummary }}</span>
            </div>
          </template>

          <n-form label-placement="left" label-width="120px" size="small">
            <n-form-item label="LLM 配置">
              <n-select
                v-model:value="llmConfigId"
                :options="llmOptions"
                :loading="loadingLLM"
                placeholder="使用项目默认"
                clearable
                :consistent-menu-width="false"
              />
            </n-form-item>

            <n-form-item label="Token 预算">
              <n-input-number
                v-model:value="tokenBudget"
                :min="1000"
                :max="10_000_000"
                :step="1000"
                placeholder="复用环境默认"
                clearable
                style="width: 220px"
              />
              <span class="text-xs text-tertiary ml-2">
                环境默认：{{ environmentDefaultBudget?.toLocaleString() ?? "—" }}
              </span>
            </n-form-item>

            <n-form-item label="执行模式">
              <n-radio-group v-model:value="mode">
                <n-radio value="normal">正常</n-radio>
                <n-radio value="debug">调试（每步暂停）</n-radio>
              </n-radio-group>
            </n-form-item>

            <n-form-item label="数据策略">
              <n-checkbox v-model:checked="strictDataMode">
                严格模式（缺料拒绝执行，不让 AI 自造）
              </n-checkbox>
            </n-form-item>
          </n-form>
        </n-collapse-item>
      </n-collapse>

      <!-- ── 缺料 banner ────────────────────────────────────── -->
      <missing-data-banner
        :missing-keys="missingKeys"
        :details="missingDetails"
        :strict-mode="strictDataMode"
        class="mt-3"
      />
    </n-spin>

    <template #footer>
      <div class="exec-dialog__footer">
        <n-text depth="3" class="text-xs">
          {{ submitDescription }}
        </n-text>
        <div class="flex gap-2">
          <n-button quaternary :disabled="submitting" @click="visible = false">
            取消
          </n-button>
          <n-button
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="submit"
          >
            <template #icon><span class="i-carbon-play" /></template>
            立即执行
          </n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpin,
  NText,
  useMessage,
} from "naive-ui";
import {
  computeStateHealth,
  createExecutionApi,
  getRecentConfigApi,
  listEnvironmentsApi,
  missingCheckApi,
  preflightModulesApi,
  previewMergeApi,
  type ExecutionMode,
  type MergedItem,
  type MissingAlert,
  type PreflightModuleItem,
  type RecentExecutionConfig,
  type StateHealth,
  type TestEnvironment,
} from "@/services/uiAutomation";
import { recommendSetsApi, type RecommendedSet } from "@/services/testData";
import { getLLMConfigsApi, type LLMConfigInfo } from "@/services/llm";
import { useProjectStore } from "@/stores/project";
import DataMergePreview from "./DataMergePreview.vue";
import DataRecommendation from "./DataRecommendation.vue";
import MissingDataBanner from "./MissingDataBanner.vue";

const props = defineProps<{
  /** 弹窗 v-model:show */
  show: boolean;
  /** 选中的用例 id 列表（必传，且非空） */
  testcaseIds: string[];
}>();

const emit = defineEmits<{
  (e: "update:show", v: boolean): void;
  /** 执行已派发，父组件可清理选择 / 关闭悬浮等。携带 ExecutionListItem.id */
  (e: "submitted", executionId: string): void;
}>();

const router = useRouter();
const message = useMessage();
const projectStore = useProjectStore();

const visible = computed<boolean>({
  get: () => props.show,
  set: (v) => emit("update:show", v),
});

const title = computed(() => "执行 UI 自动化测试");

// ─── 一级状态：弹窗整体 ────────────────────────────────────────────

const loadingInitial = ref(false);
const submitting = ref(false);

// ─── 环境 ──────────────────────────────────────────────────────────

const environments = shallowRef<TestEnvironment[]>([]);
const loadingEnvs = ref(false);
const environmentId = ref<string | null>(null);

const environmentOptions = computed(() =>
  environments.value.map((e) => ({
    label: e.name,
    value: e.id,
  })),
);

const environmentPlaceholder = computed(() => {
  if (loadingEnvs.value) return "加载中…";
  if (environments.value.length === 0) return "尚未配置环境";
  return "选择环境";
});

const selectedEnv = computed<TestEnvironment | null>(() =>
  environments.value.find((e) => e.id === environmentId.value) ?? null,
);

const selectedEnvHealth = computed<StateHealth | null>(() => {
  const env = selectedEnv.value;
  if (!env) return null;
  return computeStateHealth(env.state_saved_at);
});

const environmentDefaultBudget = computed(() =>
  selectedEnv.value?.token_budget ?? null,
);

function stateIconFor(kind: StateHealth["kind"]) {
  switch (kind) {
    case "fresh":
      return "i-carbon-checkmark-filled text-success";
    case "stale":
      return "i-carbon-warning-alt-filled text-warning";
    default:
      return "i-carbon-circle-dash text-tertiary";
  }
}

// ─── LLM 配置 ──────────────────────────────────────────────────────

const llmConfigs = shallowRef<LLMConfigInfo[]>([]);
const loadingLLM = ref(false);
const llmConfigId = ref<string | null>(null);

const llmOptions = computed(() =>
  llmConfigs.value.map((c) => ({
    label: `${c.name}${c.is_default ? "（默认）" : ""}`,
    value: c.id,
  })),
);

// ─── 物料 ──────────────────────────────────────────────────────────

const recommendations = shallowRef<RecommendedSet[]>([]);
const loadingRecommendations = ref(false);
const loadedSetIds = ref<string[]>([]);
const manualOverrides = ref<Record<string, unknown>>({});

const mergedItems = shallowRef<MergedItem[]>([]);
const loadingPreview = ref(false);

const missingKeys = ref<string[]>([]);
const missingDetails = ref<MissingAlert[]>([]);
const loadingMissing = ref(false);

// ─── 测试地址（按模块） ────────────────────────────────────────────

const preflightModules = shallowRef<PreflightModuleItem[]>([]);
const loadingModules = ref(false);
// 用户在 UI 上"覆盖"过的 module_id → 输入框当前值。空串=显式清空。
// 关键约定：只有这个 map 里的 key 才会被 submit 到后端 module_entry_overrides；
// 用户没动过的模块就让后端用 DB 里持久存的 entry_path（不传等于不覆盖）。
const moduleEntryOverrides = ref<Record<string, string>>({});

// ─── 高级 ──────────────────────────────────────────────────────────

const tokenBudget = ref<number | null>(null);
const mode = ref<ExecutionMode>("normal");
const strictDataMode = ref(false);

// ─── 复用上次 ──────────────────────────────────────────────────────

const recentConfig = shallowRef<RecentExecutionConfig | null>(null);
const recentApplied = ref(false);

const canReuseRecent = computed(
  () => recentConfig.value !== null && !recentApplied.value,
);

// ─── 派生 UI 文案 ──────────────────────────────────────────────────

const budgetSummary = computed(() => {
  const v = tokenBudget.value ?? environmentDefaultBudget.value ?? 0;
  if (v <= 0) return "预算未设";
  if (v >= 1000) return `预算 ${(v / 1000).toFixed(0)}K tokens`;
  return `预算 ${v} tokens`;
});

const advancedSummary = computed(() => {
  const parts: string[] = [];
  parts.push(mode.value === "debug" ? "调试模式" : "正常");
  if (llmConfigId.value) {
    const cfg = llmConfigs.value.find((c) => c.id === llmConfigId.value);
    if (cfg) parts.push(`LLM:${cfg.name}`);
  } else {
    parts.push("LLM:默认");
  }
  if (strictDataMode.value) parts.push("严格物料");
  return parts.join(" · ");
});

const targetsSummary = computed(() => {
  const total = preflightModules.value.length;
  if (total === 0) return "—";
  const overridden = Object.keys(moduleEntryOverrides.value).length;
  const configured = preflightModules.value.filter((m) => {
    if (!m.module_id) return false;
    const ov = moduleEntryOverrides.value[m.module_id];
    if (ov !== undefined) return ov.trim().length > 0;
    return !!(m.entry_path && m.entry_path.trim());
  }).length;
  const parts = [`${total} 个模块`];
  parts.push(`${configured} 个已配置入口`);
  if (overridden > 0) parts.push(`${overridden} 个已临时覆盖`);
  return parts.join(" · ");
});

function entryInputValue(m: PreflightModuleItem): string {
  if (!m.module_id) return "";
  const ov = moduleEntryOverrides.value[m.module_id];
  if (ov !== undefined) return ov;
  return m.entry_path ?? "";
}

function effectiveEntryPath(m: PreflightModuleItem): string | null {
  if (!m.module_id) return null;
  const ov = moduleEntryOverrides.value[m.module_id];
  if (ov !== undefined) {
    return ov.trim() || null;
  }
  return (m.entry_path ?? "").trim() || null;
}

/** 拼出预览的"实际目标 URL"——给用户看：base_url + entry_path 后到底跑哪。 */
function resolvedTargetUrl(m: PreflightModuleItem): string | null {
  const entry = effectiveEntryPath(m);
  if (!entry) return null;
  if (entry.startsWith("http://") || entry.startsWith("https://")) {
    return entry;
  }
  const base = (selectedEnv.value?.base_url ?? "").replace(/\/+$/, "");
  if (!base) return entry;
  return `${base}/${entry.replace(/^\/+/, "")}`;
}

function updateEntryOverride(m: PreflightModuleItem, raw: string) {
  if (!m.module_id) return;
  const original = m.entry_path ?? "";
  // 等于 DB 原值时清掉 override（避免发一次没意义的覆盖请求）
  if (raw === original) {
    delete moduleEntryOverrides.value[m.module_id];
    moduleEntryOverrides.value = { ...moduleEntryOverrides.value };
    return;
  }
  moduleEntryOverrides.value = {
    ...moduleEntryOverrides.value,
    [m.module_id]: raw,
  };
}

const submitDescription = computed(() => {
  if (strictDataMode.value && missingKeys.value.length > 0) {
    return `严格模式：还有 ${missingKeys.value.length} 项缺料，无法执行`;
  }
  if (missingKeys.value.length > 0) {
    return `${missingKeys.value.length} 项缺料将由 AI 自造数据兜底`;
  }
  return "全部物料已就绪，可立即执行";
});

const canSubmit = computed(
  () =>
    !submitting.value &&
    !loadingInitial.value &&
    environmentId.value !== null &&
    props.testcaseIds.length > 0 &&
    !(strictDataMode.value && missingKeys.value.length > 0),
);

// ─── 数据加载 ──────────────────────────────────────────────────────

async function loadEnvironments() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loadingEnvs.value = true;
  try {
    // 一期项目通常 1-3 个环境，page_size=200 一次拉完
    const res = await listEnvironmentsApi(projectId, { page: 1, page_size: 200 });
    if (res.success) {
      environments.value = res.data.items;
      // 默认选中第一个；若 recentConfig 提供了 environment_id 后续 hydrate 会覆盖
      if (!environmentId.value && environments.value.length > 0) {
        environmentId.value = environments.value[0].id;
      }
    }
  } catch {
    message.error("加载执行环境失败");
  } finally {
    loadingEnvs.value = false;
  }
}

async function loadLLMConfigs() {
  loadingLLM.value = true;
  try {
    const res = await getLLMConfigsApi();
    if (res.success) {
      llmConfigs.value = res.data;
    }
  } catch {
    /* 不阻断；用户改高级时再提示 */
  } finally {
    loadingLLM.value = false;
  }
}

// 标记 env_default 集合是不是已经被用户「主动取消勾选」过——避免环境切换
// 后又把用户已经手动取消的默认集自动勾回来（典型场景：用户切环境想纯净
// 跑，先取消默认，又改了一次推荐刷新就又被勾上）。
const envDefaultDeselected = ref<Set<string>>(new Set());

async function loadRecommendations() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loadingRecommendations.value = true;
  try {
    const res = await recommendSetsApi(projectId, {
      testcase_ids: props.testcaseIds,
      environment_id: environmentId.value,
      top_n: 10,
    });
    if (res.success) {
      const prev = recommendations.value ?? [];
      recommendations.value = res.data.items;
      // 自动勾选 env_default + project_default + testcase_default —— "零必填
      // 一秒确认"哲学：用户不动手，默认配置就能跑通；env 默认集被列入勾选
      // 后用户依然可以取消（验收反馈：环境选了默认集后弹窗下方应自动勾上）。
      const autoTags = new Set(["env_default", "project_default", "testcase_default"]);
      const autoSelectIds = res.data.items
        .filter((r) => autoTags.has(r.reason_code))
        .map((r) => r.set.id);

      const prevAutoIds = new Set(
        prev.filter((r) => autoTags.has(r.reason_code)).map((r) => r.set.id),
      );
      const nextSelected = new Set(loadedSetIds.value);
      // 把上一次的「自动勾选项」中那些已经不再属于自动集合的去掉，避免环境
      // 切换后旧 env_default 残留。
      for (const id of prevAutoIds) {
        if (!autoSelectIds.includes(id)) {
          nextSelected.delete(id);
        }
      }
      // 加入本次的自动项；用户已经主动取消的不再补回。
      for (const id of autoSelectIds) {
        if (envDefaultDeselected.value.has(id)) continue;
        nextSelected.add(id);
      }
      loadedSetIds.value = Array.from(nextSelected);
    }
  } catch {
    /* 推荐失败不阻断 */
  } finally {
    loadingRecommendations.value = false;
  }
}

async function loadPreflightModules() {
  const projectId = projectStore.currentProjectId;
  if (!projectId || props.testcaseIds.length === 0) {
    preflightModules.value = [];
    return;
  }
  loadingModules.value = true;
  try {
    const res = await preflightModulesApi(projectId, props.testcaseIds);
    if (res.success) {
      preflightModules.value = res.data.items;
    }
  } catch {
    /* preflight 失败不阻断；提交时也允许跑（行为退回现状） */
    preflightModules.value = [];
  } finally {
    loadingModules.value = false;
  }
}

async function loadRecentConfig() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  try {
    const res = await getRecentConfigApi(projectId, props.testcaseIds);
    if (res.success) {
      recentConfig.value = res.data.config;
    }
  } catch {
    recentConfig.value = null;
  }
}

/**
 * 轻量防抖：物料 / 覆盖 / 用例发生变化时 250ms 后重新拉合并 + 缺料预检。
 * 不用 lodash 是因为只此一处用，不值得引入依赖。
 */
let previewTimer: ReturnType<typeof setTimeout> | null = null;

function schedulePreviewRefresh() {
  if (previewTimer) clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    refreshPreview();
    refreshMissing();
  }, 250);
}

async function refreshPreview() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loadingPreview.value = true;
  try {
    const res = await previewMergeApi(projectId, {
      set_ids: loadedSetIds.value,
      environment_id: environmentId.value,
      testcase_ids: props.testcaseIds,
      manual_overrides: manualOverrides.value,
    });
    if (res.success) {
      mergedItems.value = res.data.items;
    }
  } catch {
    mergedItems.value = [];
  } finally {
    loadingPreview.value = false;
  }
}

async function refreshMissing() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loadingMissing.value = true;
  try {
    const res = await missingCheckApi(projectId, {
      set_ids: loadedSetIds.value,
      environment_id: environmentId.value,
      testcase_ids: props.testcaseIds,
      manual_overrides: manualOverrides.value,
    });
    if (res.success) {
      missingKeys.value = res.data.missing_keys;
      missingDetails.value = res.data.details;
    }
  } catch {
    missingKeys.value = [];
    missingDetails.value = [];
  } finally {
    loadingMissing.value = false;
  }
}

// ─── 事件处理 ──────────────────────────────────────────────────────

function handleEnvironmentChange(value: string | null) {
  environmentId.value = value;
  // 切换环境时重新拉推荐：env_default 列表会随之变化，需要把新环境
  // 的默认集勾上、旧环境的勾掉。同时清空 deselected 记忆——切到新
  // 环境用户应当看到完整的「自动勾选」体验。
  envDefaultDeselected.value = new Set();
  loadRecommendations();
  schedulePreviewRefresh();
}

function goCreateEnvironment() {
  // 关闭弹窗 + 跳转到当前项目的环境管理页（用 ForProject 路由确保 URL 带 projectId）。
  // 用户在环境页创建完，回退到测试用例页重新发起即可。
  visible.value = false;
  const projectId = projectStore.currentProjectId;
  if (!projectId) {
    router.push({ name: "UIEnvironmentList" });
    return;
  }
  router.push({
    name: "UIEnvironmentListForProject",
    params: { projectId },
  });
}

function handleSelectedSetsChange(ids: string[]) {
  // 记录用户主动取消的「自动勾选」集合，避免下次刷新推荐时又被强制勾回来。
  const before = new Set(loadedSetIds.value);
  const after = new Set(ids);
  const autoTags = new Set(["env_default", "project_default", "testcase_default"]);
  for (const rec of recommendations.value ?? []) {
    if (!autoTags.has(rec.reason_code)) continue;
    if (before.has(rec.set.id) && !after.has(rec.set.id)) {
      envDefaultDeselected.value.add(rec.set.id);
    } else if (!before.has(rec.set.id) && after.has(rec.set.id)) {
      // 用户重新勾上了 → 取消「拒绝自动勾选」标记
      envDefaultDeselected.value.delete(rec.set.id);
    }
  }
  loadedSetIds.value = ids;
  schedulePreviewRefresh();
}

function handleOverridesChange(v: Record<string, unknown>) {
  manualOverrides.value = v;
  schedulePreviewRefresh();
}

function reuseRecentConfig() {
  const cfg = recentConfig.value;
  if (!cfg) return;
  if (cfg.environment_id) environmentId.value = cfg.environment_id;
  loadedSetIds.value = [...(cfg.loaded_set_ids ?? [])];
  manualOverrides.value = { ...(cfg.manual_overrides ?? {}) };
  llmConfigId.value = cfg.llm_config_id;
  tokenBudget.value = cfg.token_budget_override;
  strictDataMode.value = !!cfg.strict_data_mode;
  if (cfg.mode === "debug" || cfg.mode === "normal") mode.value = cfg.mode;
  recentApplied.value = true;
  schedulePreviewRefresh();
  message.success("已复用上次执行配置");
}

async function submit() {
  if (!canSubmit.value) return;
  const projectId = projectStore.currentProjectId;
  if (!projectId) {
    message.error("请先在顶栏选择项目");
    return;
  }
  submitting.value = true;
  try {
    const body = {
      testcase_ids: props.testcaseIds,
      environment_id: environmentId.value,
      mode: mode.value,
      llm_config_id: llmConfigId.value,
      loaded_set_ids: loadedSetIds.value,
      manual_overrides: manualOverrides.value,
      token_budget: tokenBudget.value,
      strict_data_mode: strictDataMode.value,
      // 只把"用户在 UI 上动过"的 module override 传给后端；没动过的让后端
      // 沿用 module.entry_path（DB 里持久化的值）。
      module_entry_overrides:
        Object.keys(moduleEntryOverrides.value).length > 0
          ? { ...moduleEntryOverrides.value }
          : undefined,
    };
    const res = await createExecutionApi(projectId, body);
    if (res.success) {
      message.success("执行已派发，正在打开监控页…");
      const execId = res.data.id;
      emit("submitted", execId);
      visible.value = false;
      // 跳转至 Task 10.2 即将实现的监控页；当前 placeholder 也能展示状态
      router.push({
        name: "UIExecutionMonitor",
        params: { projectId, execId },
      });
    }
  } catch (err) {
    const msg =
      typeof err === "object" && err !== null && "message" in err
        ? String((err as { message: unknown }).message)
        : "派发执行失败";
    message.error(msg);
  } finally {
    submitting.value = false;
  }
}

// ─── 弹窗生命周期 ──────────────────────────────────────────────────

async function bootstrap() {
  loadingInitial.value = true;
  try {
    // 第一波：环境 / LLM / 复用历史 / 模块预检——彼此独立可并行
    await Promise.all([
      loadEnvironments(),
      loadLLMConfigs(),
      loadRecentConfig(),
      loadPreflightModules(),
    ]);
    // 推荐物料集需要 environment_id 已就位（env_default 来自所选环境）。
    // ``loadEnvironments`` 完成后 environmentId.value 才会被填上默认环境，
    // 所以推荐必须放到第二波。
    await loadRecommendations();
    // 第一帧合并预览：等环境就位 + 默认推荐勾上后再拉
    await refreshPreview();
    await refreshMissing();
  } finally {
    loadingInitial.value = false;
  }
}

function resetState() {
  environmentId.value = null;
  llmConfigId.value = null;
  loadedSetIds.value = [];
  manualOverrides.value = {};
  mergedItems.value = [];
  missingKeys.value = [];
  missingDetails.value = [];
  recommendations.value = [];
  envDefaultDeselected.value = new Set();
  tokenBudget.value = null;
  mode.value = "normal";
  strictDataMode.value = false;
  recentConfig.value = null;
  recentApplied.value = false;
  preflightModules.value = [];
  moduleEntryOverrides.value = {};
}

// 弹窗显示/隐藏的副作用：监听 props.show 而不是 modal 的 @update:show 事件。
//
// 历史 bug（v3.0.2 修复）：原本写的是 <n-modal v-model:show ... @update:show="handleShow">,
// 期望弹窗每次打开就触发 bootstrap。但 @update:show 只在 modal 内部触发关闭/打开（点 mask、
// 点关闭、ESC 等）时才 emit；当父组件直接把 :show 从 false 设到 true 时，update:show
// 不会触发。结果 bootstrap 永远没跑，loadEnvironments / loadLLMConfigs 没发请求，
// 环境下拉永远空。watch props.show 才是稳健的做法 —— 无论谁触发都能感知。
watch(
  () => props.show,
  (open) => {
    if (open) {
      resetState();
      bootstrap();
    } else if (previewTimer) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
  },
  { immediate: true },
);

// 当外部传入新的 testcaseIds（用户改了选择再次打开）时刷新预览
watch(
  () => props.testcaseIds,
  () => {
    if (visible.value) {
      schedulePreviewRefresh();
      loadRecommendations();
      loadRecentConfig();
      loadPreflightModules();
    }
  },
);
</script>

<style scoped>
.exec-dialog__header-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0 12px;
  flex-wrap: wrap;
}

.exec-dialog__count {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  border-radius: var(--radius-pill);
  font-size: 13px;
}

.exec-dialog__env {
  width: 200px;
}

.exec-dialog__state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.exec-dialog__budget {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.exec-dialog__reuse {
  margin-left: auto;
}

.exec-dialog__collapse {
  margin-top: 8px;
}

.exec-dialog__section-header {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
}

.exec-dialog__material {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.exec-dialog__material-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.exec-dialog__block-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.exec-dialog__material-foot {
  border-top: 1px dashed var(--border-subtle);
  padding-top: 8px;
}

.exec-dialog__module-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.exec-dialog__module-row {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 8px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  background: var(--bg-secondary);
}

.exec-dialog__module-meta {
  flex: 1 1 auto;
  min-width: 0;
}

.exec-dialog__module-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.exec-dialog__module-resolved {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-all;
}

.exec-dialog__module-resolved code {
  background: var(--bg-tertiary);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11.5px;
}

.exec-dialog__module-resolved--empty {
  color: var(--text-tertiary);
  font-style: italic;
}

.exec-dialog__module-input {
  flex: 0 0 320px;
}

.exec-dialog__module-foot {
  margin-top: 10px;
  border-top: 1px dashed var(--border-subtle);
  padding-top: 8px;
}

.exec-dialog__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.text-tertiary {
  color: var(--text-tertiary);
}

.text-warning {
  color: var(--color-warning, #b45309);
}

.text-success {
  color: var(--color-success, #16a34a);
}
</style>
