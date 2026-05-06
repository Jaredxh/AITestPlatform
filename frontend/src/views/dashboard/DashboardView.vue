<template>
  <div>
    <page-header
      title="概览"
      icon="i-carbon-dashboard"
      :subtitle="projectStore.currentProject ? `项目: ${projectStore.currentProject.name}` : '全部项目'"
    >
      <template #extra>
        <n-button quaternary :loading="loading" @click="fetchAll">
          <template #icon><span class="i-carbon-renew" /></template>
          刷新
        </n-button>
      </template>
    </page-header>

    <n-spin :show="loading">
      <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
        <n-gi span="4 m:1">
          <n-card hoverable class="stat-card">
            <div class="stat-card__inner">
              <div class="stat-icon bg-blue-50 dark:bg-blue-900/30 text-blue-500">
                <span class="i-carbon-folder text-xl" />
              </div>
              <div class="stat-card__body">
                <div class="stat-card__num">{{ stats.project_count }}</div>
                <div class="stat-card__label">项目总数</div>
                <div class="stat-card__hint">所有可见项目</div>
              </div>
            </div>
          </n-card>
        </n-gi>

        <n-gi span="4 m:1">
          <n-card hoverable class="stat-card">
            <div class="stat-card__inner">
              <div class="stat-icon bg-purple-50 dark:bg-purple-900/30 text-purple-500">
                <span class="i-carbon-document text-xl" />
              </div>
              <div class="stat-card__body">
                <div class="stat-card__num">{{ stats.document_count }}</div>
                <div class="stat-card__label">需求文档</div>
                <div class="stat-card__hint">
                  <template v-if="stats.document_parsed_count > 0">
                    已解析 <span class="text-green-500">{{ stats.document_parsed_count }}</span>
                  </template>
                  <template v-else>暂未上传</template>
                </div>
              </div>
            </div>
          </n-card>
        </n-gi>

        <n-gi span="4 m:1">
          <n-card hoverable class="stat-card">
            <div class="stat-card__inner">
              <div class="stat-icon bg-emerald-50 dark:bg-emerald-900/30 text-emerald-500">
                <span class="i-carbon-task text-xl" />
              </div>
              <div class="stat-card__body">
                <div class="stat-card__num">{{ stats.testcase_count }}</div>
                <div class="stat-card__label">测试用例</div>
                <div class="stat-card__hint">
                  <template v-if="stats.testcase_count > 0">
                    手动 {{ stats.testcase_manual_count }} · AI {{ stats.testcase_ai_count }}
                  </template>
                  <template v-else>暂无用例</template>
                </div>
              </div>
            </div>
          </n-card>
        </n-gi>

        <n-gi span="4 m:1">
          <n-card hoverable class="stat-card">
            <div class="stat-card__inner">
              <div class="stat-icon bg-amber-50 dark:bg-amber-900/30 text-amber-500">
                <span class="i-carbon-analytics text-xl" />
              </div>
              <div class="stat-card__body">
                <div class="stat-card__num">{{ stats.review_count }}</div>
                <div class="stat-card__label">AI 评审</div>
                <div class="stat-card__hint">
                  <template v-if="stats.review_avg_score != null">
                    平均 <span :class="scoreTextClass">{{ stats.review_avg_score }} 分</span>
                  </template>
                  <template v-else>暂无评审</template>
                </div>
              </div>
            </div>
          </n-card>
        </n-gi>
      </n-grid>

      <n-grid :cols="3" :x-gap="16" :y-gap="16" class="mt-4" responsive="screen" item-responsive>
        <n-gi span="3 m:1">
          <n-card title="用例优先级分布" size="small" class="info-card">
            <div v-if="stats.testcase_count > 0" class="info-card__body space-y-3">
              <div v-for="p in priorityBars" :key="p.key" class="flex items-center gap-3">
                <n-tag :type="p.tagType" size="small" :bordered="false" class="w-10 text-center justify-center">
                  {{ p.label }}
                </n-tag>
                <n-progress
                  :percentage="p.pct"
                  :color="p.color"
                  :rail-color="'rgba(128,128,128,0.1)'"
                  :show-indicator="false"
                  :height="12"
                  class="flex-1"
                  style="border-radius: 6px;"
                />
                <span class="text-xs text-gray-500 w-12 text-right">{{ p.count }}</span>
              </div>
            </div>
            <n-empty v-else size="small" description="暂无用例数据" class="info-card__empty" />
          </n-card>
        </n-gi>

        <n-gi span="3 m:1">
          <n-card title="用例执行结果" size="small" class="info-card">
            <div v-if="stats.testcase_count > 0" class="info-card__body">
              <div class="exec-summary">
                <div
                  v-for="e in execBars"
                  :key="e.key"
                  class="exec-summary__cell"
                  :style="{ '--cell-color': e.color }"
                >
                  <div class="exec-summary__num">{{ e.count }}</div>
                  <div class="exec-summary__label">
                    <span :class="e.icon" class="mr-1" />{{ e.label }}
                  </div>
                </div>
              </div>
              <div class="exec-bar">
                <div
                  v-for="e in execBars"
                  :key="e.key"
                  class="exec-bar__seg"
                  :title="`${e.label} ${e.count} (${e.pct.toFixed(0)}%)`"
                  :style="{ width: e.pct + '%', background: e.color }"
                />
              </div>
              <div class="exec-summary__hint">
                通过率 <span class="font-medium" :style="{ color: '#18a058' }">{{ passRate.toFixed(0) }}%</span>
              </div>
            </div>
            <n-empty v-else size="small" description="暂无用例数据" class="info-card__empty" />
          </n-card>
        </n-gi>

        <n-gi span="3 m:1">
          <n-card title="平台数据" size="small" class="info-card">
            <div class="info-card__body platform-list">
              <div class="platform-row">
                <span class="platform-row__label">
                  <span class="i-carbon-catalog text-gray-400" />
                  <span class="text-sm">模块目录</span>
                </span>
                <span class="platform-row__value">{{ stats.module_count }}</span>
              </div>
              <div class="platform-row">
                <span class="platform-row__label">
                  <span class="i-carbon-magic-wand text-gray-400" />
                  <span class="text-sm">AI 生成批次</span>
                </span>
                <span class="platform-row__value">{{ stats.generation_batch_count }}</span>
              </div>
              <div class="platform-row">
                <span class="platform-row__label">
                  <span class="i-carbon-chat-bot text-gray-400" />
                  <span class="text-sm">对话会话</span>
                </span>
                <span class="platform-row__value">{{ stats.chat_session_count }}</span>
              </div>
              <div class="platform-row">
                <span class="platform-row__label">
                  <span class="i-carbon-checkmark-filled text-gray-400" />
                  <span class="text-sm">完成评审</span>
                </span>
                <span class="platform-row__value">{{ stats.review_completed_count }}</span>
              </div>
              <div class="platform-row">
                <span class="platform-row__label">
                  <span class="i-carbon-analytics text-gray-400" />
                  <span class="text-sm">AI 生成用例占比</span>
                </span>
                <span class="platform-row__value">{{ aiPct.toFixed(0) }}%</span>
              </div>
            </div>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- ── Task 11.1：UI 自动化执行统计（项目维度，需要选中项目）──
           设计取舍：
           - 仅当 projectStore.currentProjectId 存在时显示。"全部项目"视图下
             跨项目聚合没有"业务通过率"语义（不同项目的 data_failure 比率天差地别），
             而且后端这个接口本身就是项目维度的。
           - 业务/执行视图切换不重新请求：后端两个口径同时返回。
           - 没有数据时不渲染整个块，避免空白尴尬，以及用户被空状态误导。 -->
      <n-card
        v-if="projectStore.currentProjectId && uiStats.execution_count > 0"
        title="UI 自动化执行"
        size="small"
        class="mt-4 ui-stats-card"
      >
        <template #header-extra>
          <div class="ui-stats-card__view-toggle">
            <n-radio-group v-model:value="uiStatsView" size="small">
              <n-radio-button value="task">任务视图</n-radio-button>
              <n-radio-button value="business">业务视图</n-radio-button>
              <n-radio-button value="execution">执行视图</n-radio-button>
            </n-radio-group>
            <span class="text-xs text-gray-400 ml-2">{{ viewHint }}</span>
          </div>
        </template>

        <!-- KPI 行 -->
        <n-grid :cols="4" :x-gap="12" :y-gap="12" responsive="screen" item-responsive>
          <n-gi span="4 m:1">
            <!-- 验收反馈：64px 圆环里塞 indicator 文本会跟环线重叠（小尺寸文字
                 撑满），且 case-level 口径无法反映"前置步骤失败 → 0 用例产出"
                 这种场景。改成：左边圆环不显示百分比文字，右边大字独立显示
                 当前视图的通过率（业务/执行/任务）。任务级 = 成功执行数/总
                 终态执行数，回答用户视角的「整体通过率」。 -->
            <div class="ui-stats-kpi ui-stats-kpi--rate">
              <n-progress
                type="circle"
                :percentage="currentPassRate"
                :color="rateColor(currentPassRate)"
                :rail-color="'rgba(128,128,128,0.15)'"
                :stroke-width="10"
                :show-indicator="false"
                style="width: 56px; flex-shrink: 0;"
              />
              <div class="ui-stats-kpi__rate-body">
                <div
                  class="ui-stats-kpi__rate-value"
                  :style="{ color: rateColor(currentPassRate) }"
                >
                  {{ currentPassRate.toFixed(1) }}%
                </div>
                <div class="ui-stats-kpi__label">{{ rateLabel }}</div>
                <div class="ui-stats-kpi__sub">
                  <template v-if="uiStatsView === 'business' || uiStatsView === 'execution'">
                    {{ uiStats.passed_cases }}/{{ rateDenominator }} 用例
                    <template v-if="uiStatsView === 'business' && uiStats.excluded_data_failure_cases > 0">
                      · 排除 {{ uiStats.excluded_data_failure_cases }} 缺料
                    </template>
                  </template>
                  <template v-else>
                    {{ uiStats.succeeded_exec_count }}/{{ uiStats.execution_count }} 任务通过
                  </template>
                </div>
              </div>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="ui-stats-kpi">
              <span class="i-carbon-machine-learning-model ui-stats-kpi__icon text-blue-500" />
              <div>
                <div class="ui-stats-kpi__value">{{ uiStats.execution_count }}</div>
                <div class="ui-stats-kpi__label">执行总次数</div>
                <div class="ui-stats-kpi__sub">{{ uiStats.total_cases }} 条用例</div>
              </div>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="ui-stats-kpi">
              <span class="i-carbon-time ui-stats-kpi__icon text-amber-500" />
              <div>
                <div class="ui-stats-kpi__value">{{ formatDuration(uiStats.avg_duration_ms) }}</div>
                <div class="ui-stats-kpi__label">平均耗时/次</div>
                <div class="ui-stats-kpi__sub">单次执行</div>
              </div>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="ui-stats-kpi">
              <span class="i-carbon-meter-alt ui-stats-kpi__icon text-emerald-500" />
              <div>
                <div class="ui-stats-kpi__value">{{ formatTokens(uiStats.total_tokens) }}</div>
                <div class="ui-stats-kpi__label">Tokens 累计</div>
                <div class="ui-stats-kpi__sub">含 LLM + 工具</div>
              </div>
            </div>
          </n-gi>
        </n-grid>

        <!-- 第二行：可信度分布 + Top 10 自造 keys -->
        <n-grid :cols="2" :x-gap="16" :y-gap="16" class="mt-3" responsive="screen" item-responsive>
          <n-gi span="2 m:1">
            <div class="ui-stats-section">
              <div class="ui-stats-section__title">数据可信度分布</div>
              <div class="ui-stats-confidence-bar">
                <div
                  v-for="seg in confidenceSegments"
                  :key="seg.key"
                  class="ui-stats-confidence-bar__seg"
                  :title="`${seg.label} ${seg.count} (${seg.pct.toFixed(1)}%)`"
                  :style="{ width: seg.pct + '%', background: seg.color }"
                />
              </div>
              <div class="ui-stats-confidence-legend">
                <div
                  v-for="seg in confidenceSegments"
                  :key="seg.key"
                  class="ui-stats-confidence-legend__item"
                >
                  <span class="ui-stats-confidence-legend__dot" :style="{ background: seg.color }" />
                  <span class="text-xs">{{ seg.label }}</span>
                  <span class="text-xs font-medium ml-1">{{ seg.count }}</span>
                  <span class="text-xs text-gray-400 ml-1">({{ seg.pct.toFixed(0) }}%)</span>
                </div>
              </div>
            </div>
          </n-gi>

          <n-gi span="2 m:1">
            <div class="ui-stats-section">
              <div class="ui-stats-section__title">
                自造数据 Top 10
                <span class="text-xs text-gray-400 font-normal ml-1">
                  · 出现频次最高，建议补料
                </span>
              </div>
              <div v-if="uiStats.top_synthesized_keys.length > 0" class="ui-stats-top-keys">
                <div
                  v-for="(item, idx) in uiStats.top_synthesized_keys"
                  :key="item.key"
                  class="ui-stats-top-keys__row"
                >
                  <span class="ui-stats-top-keys__rank">#{{ idx + 1 }}</span>
                  <span class="ui-stats-top-keys__key" :title="item.key">{{ item.key }}</span>
                  <n-tag size="tiny" type="warning" :bordered="false">
                    {{ item.count }} 次
                  </n-tag>
                </div>
                <div class="ui-stats-top-keys__hint">
                  <a
                    class="text-blue-500 cursor-pointer hover:underline"
                    @click="goTestData"
                  >
                    前往「测试物料」补充以上 key →
                  </a>
                </div>
              </div>
              <n-empty
                v-else
                size="small"
                description="暂无 AI 自造数据记录"
                class="ui-stats-section__empty"
              />
            </div>
          </n-gi>
        </n-grid>

        <!-- 第三行：最近执行列表 -->
        <div class="ui-stats-section mt-3">
          <div class="ui-stats-section__title">
            最近执行
            <span class="text-xs text-gray-400 font-normal ml-1">
              · 按当前视图显示通过率
            </span>
          </div>
          <div v-if="uiStats.recent_executions.length > 0" class="ui-stats-recent">
            <div
              v-for="exec in uiStats.recent_executions"
              :key="exec.id"
              class="ui-stats-recent__row"
              @click="goExecutionDetail(exec.id)"
            >
              <span :class="execStatusIcon(exec.status)" class="ui-stats-recent__icon" />
              <div class="ui-stats-recent__main">
                <div class="ui-stats-recent__title">
                  执行 #{{ exec.id.slice(0, 8) }}
                  <n-tag
                    v-if="exec.mode === 'debug'"
                    size="tiny"
                    type="info"
                    :bordered="false"
                    class="ml-1"
                  >
                    debug
                  </n-tag>
                </div>
                <div class="ui-stats-recent__meta">
                  <n-tag :type="execStatusTagType(exec.status)" size="tiny" :bordered="false">
                    {{ execStatusLabel(exec.status) }}
                  </n-tag>
                  <span>{{ exec.passed_cases }}/{{ exec.total_cases }} 通过</span>
                  <span v-if="exec.duration_ms != null">{{ formatDuration(exec.duration_ms) }}</span>
                  <span class="text-gray-400">{{ formatRecentTime(exec) }}</span>
                </div>
              </div>
              <div class="ui-stats-recent__rate">
                <div
                  class="ui-stats-recent__rate-num"
                  :style="{ color: rateColor(currentRateOf(exec)) }"
                >
                  {{ currentRateOf(exec).toFixed(0) }}%
                </div>
                <div class="ui-stats-recent__rate-label">
                  {{ currentRateLabel() }}
                </div>
              </div>
            </div>
          </div>
          <n-empty
            v-else
            size="small"
            description="该项目暂无执行记录"
            class="ui-stats-section__empty"
          />
        </div>
      </n-card>

      <n-card title="最新活动" size="small" class="mt-4 activity-card">
        <template #header-extra>
          <n-text depth="3" class="text-xs">最近 {{ activities.length || 0 }} 条 · 评审 / 上传 / 用例 / AI 生成</n-text>
        </template>
        <div v-if="activities.length > 0" class="activity-list">
          <div
            v-for="act in activities"
            :key="`${act.type}-${act.id}`"
            class="activity-item"
          >
            <span :class="activityIcon(act.type)" class="activity-item__icon" />
            <div class="activity-item__main">
              <div class="activity-item__title">{{ act.title }}</div>
              <div class="activity-item__meta">
                <span>{{ act.user || '系统' }}</span>
                <span class="activity-item__dot">·</span>
                <span>{{ formatDate(act.created_at) }}</span>
              </div>
            </div>
            <div class="activity-item__right">
              <n-tag
                :type="activityStatusType(act.status)"
                size="tiny"
                :bordered="false"
              >
                {{ statusLabel(act.status) }}
              </n-tag>
              <span v-if="act.score != null" class="text-xs font-medium" :style="{ color: scoreColor(act.score) }">
                {{ act.score }}分
              </span>
              <span v-if="act.generated_count != null" class="text-xs text-gray-500">
                {{ act.accepted_count }}/{{ act.generated_count }} 条
              </span>
            </div>
          </div>
        </div>
        <n-empty v-else size="small" description="暂无活动记录" class="py-4" />
      </n-card>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NCard,
  NEmpty,
  NGi,
  NGrid,
  NProgress,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NTag,
  NText,
  useMessage,
} from "naive-ui";
import { request } from "@/services/request";
import { useProjectStore } from "@/stores/project";
import PageHeader from "@/components/common/PageHeader.vue";
import {
  getProjectUIStatsApi,
  type UIStatsData,
  type UIStatsRecentExecution,
  type UIStatsView,
} from "@/services/uiStats";

const message = useMessage();
const projectStore = useProjectStore();
const router = useRouter();
const loading = ref(false);

interface DashboardStats {
  project_count: number;
  document_count: number;
  document_parsed_count: number;
  review_count: number;
  review_completed_count: number;
  review_avg_score: number | null;
  testcase_count: number;
  testcase_high_count: number;
  testcase_medium_count: number;
  testcase_low_count: number;
  testcase_manual_count: number;
  testcase_ai_count: number;
  testcase_not_run_count: number;
  testcase_passed_count: number;
  testcase_failed_count: number;
  testcase_blocked_count: number;
  module_count: number;
  generation_batch_count: number;
  chat_session_count: number;
}

interface Activity {
  type: string;
  id: string;
  title: string;
  status: string;
  score?: number | null;
  generated_count?: number | null;
  accepted_count?: number | null;
  user: string | null;
  created_at: string;
}

const stats = reactive<DashboardStats>({
  project_count: 0,
  document_count: 0,
  document_parsed_count: 0,
  review_count: 0,
  review_completed_count: 0,
  review_avg_score: null,
  testcase_count: 0,
  testcase_high_count: 0,
  testcase_medium_count: 0,
  testcase_low_count: 0,
  testcase_manual_count: 0,
  testcase_ai_count: 0,
  testcase_not_run_count: 0,
  testcase_passed_count: 0,
  testcase_failed_count: 0,
  testcase_blocked_count: 0,
  module_count: 0,
  generation_batch_count: 0,
  chat_session_count: 0,
});

const activities = ref<Activity[]>([]);

// ─── Task 11.1：UI 自动化执行统计 ────────────────────────────────────────
//
// view 默认 task（任务级通过率，最贴近用户对「整体通过率」的直觉）。
// 后端三个口径都同时返回，切换 view 不重新请求；只是切换显示而已。

// 把 task 口径加到本地类型上（uiStats.ts 已支持，这里只是类型 narrow）
type LocalUIStatsView = UIStatsView | "task";

const uiStatsView = ref<LocalUIStatsView>("task");

const uiStats = reactive<UIStatsData>({
  view: "business",
  pass_rate: 0,
  business_pass_rate: 0,
  execution_pass_rate: 0,
  task_pass_rate: 0,
  succeeded_exec_count: 0,
  total_cases: 0,
  passed_cases: 0,
  failed_cases: 0,
  skipped_cases: 0,
  excluded_data_failure_cases: 0,
  confidence_distribution: { reliable: 0, synthesized: 0, data_failure: 0 },
  top_synthesized_keys: [],
  execution_count: 0,
  total_tokens: 0,
  avg_duration_ms: null,
  recent_executions: [],
});

const currentPassRate = computed(() => {
  switch (uiStatsView.value) {
    case "task":
      return uiStats.task_pass_rate;
    case "business":
      return uiStats.business_pass_rate;
    default:
      return uiStats.execution_pass_rate;
  }
});

const rateLabel = computed(() => {
  switch (uiStatsView.value) {
    case "task":
      return "任务通过率";
    case "business":
      return "业务通过率";
    default:
      return "执行通过率";
  }
});

const viewHint = computed(() => {
  switch (uiStatsView.value) {
    case "task":
      return "已成功执行数/全部终态执行数 · 含前置步骤失败的任务";
    case "business":
      return "排除缺料失败的用例 · 反映被测系统质量";
    default:
      return "原始用例通过率 · 反映测试基础设施健康度";
  }
});

const rateDenominator = computed(() =>
  uiStatsView.value === "business"
    ? Math.max(0, uiStats.total_cases - uiStats.excluded_data_failure_cases)
    : uiStats.total_cases,
);

const confidenceTotal = computed(() => {
  const c = uiStats.confidence_distribution;
  return c.reliable + c.synthesized + c.data_failure;
});

const confidenceSegments = computed(() => {
  const total = confidenceTotal.value || 1;
  const c = uiStats.confidence_distribution;
  return [
    {
      key: "reliable",
      label: "🟢 数据可信",
      count: c.reliable,
      pct: (c.reliable / total) * 100,
      color: "#18a058",
    },
    {
      key: "synthesized",
      label: "🟡 含 AI 自造",
      count: c.synthesized,
      pct: (c.synthesized / total) * 100,
      color: "#f0a020",
    },
    {
      key: "data_failure",
      label: "🟠 数据失败",
      count: c.data_failure,
      pct: (c.data_failure / total) * 100,
      color: "#d03050",
    },
  ];
});

function rateColor(rate: number): string {
  if (rate >= 80) return "#18a058";
  if (rate >= 60) return "#f0a020";
  return "#d03050";
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60_000);
  const secs = Math.floor((ms % 60_000) / 1000);
  return `${mins}m${secs}s`;
}

function formatTokens(t: number | null): string {
  if (t == null) return "—";
  if (t >= 1_000_000) return `${(t / 1_000_000).toFixed(1)}M`;
  if (t >= 1000) return `${(t / 1000).toFixed(1)}K`;
  return String(t);
}

function currentRateOf(exec: UIStatsRecentExecution): number {
  // 任务视图下，单条执行的"通过率"按是否 completed 给 100/0
  // ——这样"最近执行"列表跟顶部 KPI 口径一致。
  if (uiStatsView.value === "task") {
    return exec.status === "completed" ? 100 : 0;
  }
  return uiStatsView.value === "business"
    ? exec.business_pass_rate
    : exec.execution_pass_rate;
}

function currentRateLabel(): string {
  if (uiStatsView.value === "task") return "任务";
  if (uiStatsView.value === "business") return "业务";
  return "执行";
}

function execStatusLabel(status: string): string {
  const map: Record<string, string> = {
    completed: "已完成",
    running: "执行中",
    pending: "排队",
    queued: "排队",
    failed: "失败",
    stopped: "已停止",
    aborted_budget: "预算耗尽",
  };
  return map[status] ?? status;
}

function execStatusTagType(
  status: string,
): "success" | "info" | "warning" | "error" | "default" {
  switch (status) {
    case "completed":
      return "success";
    case "running":
    case "pending":
    case "queued":
      return "info";
    case "failed":
    case "aborted_budget":
      return "error";
    case "stopped":
      return "warning";
    default:
      return "default";
  }
}

function execStatusIcon(status: string): string {
  switch (status) {
    case "completed":
      return "i-carbon-checkmark-filled text-green-500";
    case "running":
    case "pending":
    case "queued":
      return "i-carbon-play-filled-alt text-blue-500";
    case "failed":
    case "aborted_budget":
      return "i-carbon-close-filled text-red-500";
    case "stopped":
      return "i-carbon-stop-filled-alt text-amber-500";
    default:
      return "i-carbon-circle-dash text-gray-400";
  }
}

function formatRecentTime(exec: UIStatsRecentExecution): string {
  const dt = exec.completed_at ?? exec.started_at ?? exec.created_at;
  return dt ? formatDate(dt) : "—";
}

function goExecutionDetail(execId: string) {
  const pid = projectStore.currentProjectId;
  if (!pid) return;
  router.push({
    name: "UIExecutionDetail",
    params: { projectId: pid, execId },
  });
}

function goTestData() {
  const pid = projectStore.currentProjectId;
  if (!pid) {
    router.push({ name: "TestDataViewGlobal" });
    return;
  }
  router.push({ name: "TestDataView", params: { projectId: pid } });
}

const scoreTextClass = computed(() => {
  const s = stats.review_avg_score;
  if (s == null) return "text-gray-400";
  if (s >= 80) return "text-green-500";
  if (s >= 60) return "text-amber-500";
  return "text-red-500";
});

const priorityBars = computed(() => {
  const total = stats.testcase_count || 1;
  return [
    {
      key: "high",
      label: "高",
      count: stats.testcase_high_count,
      pct: Math.round((stats.testcase_high_count / total) * 100),
      color: "#d03050",
      tagType: "error" as const,
    },
    {
      key: "medium",
      label: "中",
      count: stats.testcase_medium_count,
      pct: Math.round((stats.testcase_medium_count / total) * 100),
      color: "#f0a020",
      tagType: "warning" as const,
    },
    {
      key: "low",
      label: "低",
      count: stats.testcase_low_count,
      pct: Math.round((stats.testcase_low_count / total) * 100),
      color: "#2080f0",
      tagType: "info" as const,
    },
  ];
});

const aiPct = computed(() => {
  if (stats.testcase_count === 0) return 0;
  return (stats.testcase_ai_count / stats.testcase_count) * 100;
});

const execBars = computed(() => {
  const total = stats.testcase_count || 1;
  return [
    {
      key: "passed",
      label: "通过",
      icon: "i-carbon-checkmark-filled",
      count: stats.testcase_passed_count,
      pct: (stats.testcase_passed_count / total) * 100,
      color: "#18a058",
    },
    {
      key: "failed",
      label: "失败",
      icon: "i-carbon-close-filled",
      count: stats.testcase_failed_count,
      pct: (stats.testcase_failed_count / total) * 100,
      color: "#d03050",
    },
    {
      key: "blocked",
      label: "阻塞",
      icon: "i-carbon-warning-alt-filled",
      count: stats.testcase_blocked_count,
      pct: (stats.testcase_blocked_count / total) * 100,
      color: "#f0a020",
    },
    {
      key: "not_run",
      label: "未执行",
      icon: "i-carbon-circle-dash",
      count: stats.testcase_not_run_count,
      pct: (stats.testcase_not_run_count / total) * 100,
      color: "#94a3b8",
    },
  ];
});

const passRate = computed(() => {
  const ran = stats.testcase_passed_count + stats.testcase_failed_count + stats.testcase_blocked_count;
  if (ran === 0) return 0;
  return (stats.testcase_passed_count / ran) * 100;
});

function activityIcon(type: string): string {
  if (type === "review") return "i-carbon-analytics text-blue-500";
  if (type === "generation") return "i-carbon-magic-wand text-emerald-500";
  if (type === "upload") return "i-carbon-cloud-upload text-purple-500";
  if (type === "testcase") return "i-carbon-task text-amber-500";
  return "i-carbon-circle-dash text-gray-400";
}

function activityStatusType(status: string): "success" | "warning" | "error" | "info" {
  if (["completed", "parsed", "active", "passed"].includes(status)) return "success";
  if (["failed", "deprecated"].includes(status)) return "error";
  if (status === "blocked") return "warning";
  return "info";
}

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "#999";
  if (score >= 80) return "#18a058";
  if (score >= 60) return "#f0a020";
  return "#d03050";
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    completed: "完成",
    failed: "失败",
    pending: "进行中",
    generating: "生成中",
    parsed: "已解析",
    parse_failed: "解析失败",
    active: "有效",
    deprecated: "废弃",
    draft: "草稿",
    passed: "通过",
    blocked: "阻塞",
  };
  return map[status] || status;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay} 天前`;
  return d.toLocaleDateString("zh-CN");
}

async function fetchStats() {
  const pid = projectStore.currentProjectId;
  const qs = pid ? `?project_id=${pid}` : "";
  try {
    const res = await request<{ success: boolean; data: DashboardStats }>(
      `/dashboard/stats${qs}`,
    );
    if (res.success) {
      Object.assign(stats, res.data);
    }
  } catch {
    message.error("获取统计数据失败");
  }
}

async function fetchActivities() {
  const pid = projectStore.currentProjectId;
  const qs = pid ? `?project_id=${pid}` : "";
  try {
    const res = await request<{ success: boolean; data: Activity[] }>(
      `/dashboard/recent-activity${qs}`,
    );
    if (res.success) {
      activities.value = res.data;
    }
  } catch {
    /* ignore */
  }
}

async function fetchUIStats() {
  const pid = projectStore.currentProjectId;
  // 全局视图（未选项目）下，UI 统计无意义（业务通过率分母按项目语义算）。
  // 这里直接清空既有数据，让卡片不显示，避免显示上一次项目的残留。
  if (!pid) {
    Object.assign(uiStats, {
      view: "business",
      pass_rate: 0,
      business_pass_rate: 0,
      execution_pass_rate: 0,
      task_pass_rate: 0,
      succeeded_exec_count: 0,
      total_cases: 0,
      passed_cases: 0,
      failed_cases: 0,
      skipped_cases: 0,
      excluded_data_failure_cases: 0,
      confidence_distribution: { reliable: 0, synthesized: 0, data_failure: 0 },
      top_synthesized_keys: [],
      execution_count: 0,
      total_tokens: 0,
      avg_duration_ms: null,
      recent_executions: [],
    });
    return;
  }
  try {
    // 后端 view 参数仅认 business / execution（决定 ``pass_rate`` 字段透传哪个）；
    // task 是纯前端视图，三个口径在 response 里都已带回，本地切换即可。
    const apiView: UIStatsView =
      uiStatsView.value === "task" ? "business" : uiStatsView.value;
    const res = await getProjectUIStatsApi(pid, {
      view: apiView,
      recent_limit: 8,
    });
    if (res.success) {
      Object.assign(uiStats, res.data);
    }
  } catch {
    /* 不阻断整页：UI 统计失败时留空白比 toast 噪音更克制 */
  }
}

async function fetchAll() {
  loading.value = true;
  try {
    await Promise.all([fetchStats(), fetchActivities(), fetchUIStats()]);
  } finally {
    loading.value = false;
  }
}

watch(() => projectStore.currentProjectId, fetchAll);
onMounted(fetchAll);
</script>

<style scoped>
.stat-card {
  height: 116px;
  transition:
    transform var(--duration-base) var(--easing-standard),
    box-shadow var(--duration-base) var(--easing-standard),
    border-color var(--duration-base) var(--easing-standard);
}
.stat-card:deep(.n-card__content) {
  display: flex;
  align-items: center;
  height: 100%;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
  border-color: var(--border-default);
}
.stat-card__inner {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
}
.stat-card__body {
  flex: 1;
  min-width: 0;
}
.stat-card__num {
  font-size: 26px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.1;
}
.stat-card__label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.stat-card__hint {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 4px;
  min-height: 14px;
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.info-card {
  min-height: 240px;
  display: flex;
  flex-direction: column;
}
.info-card:deep(.n-card__content) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.info-card__body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  justify-content: center;
  min-width: 0;
}
.info-card__empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 平台数据：左标签可省略、右数值不被挤出 */
.platform-list {
  gap: 12px;
}
.platform-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.platform-row__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1 1 auto;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.platform-row__value {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.exec-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.exec-summary__cell {
  text-align: center;
  border-radius: 8px;
  padding: 8px 4px;
  background: color-mix(in srgb, var(--cell-color) 8%, transparent);
}
.exec-summary__num {
  font-size: 22px;
  font-weight: 700;
  color: var(--cell-color);
  line-height: 1.1;
}
.exec-summary__label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 2px;
  display: inline-flex;
  align-items: center;
}
.exec-summary__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: right;
}

.exec-bar {
  display: flex;
  width: 100%;
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(148, 163, 184, 0.18);
}
.exec-bar__seg {
  height: 100%;
  transition: width 0.6s ease;
}

.activity-card :deep(.n-card__content) {
  padding-top: 4px;
  padding-bottom: 4px;
}
.activity-list {
  display: flex;
  flex-direction: column;
}
.activity-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--border-subtle);
}
.activity-item:last-child {
  border-bottom: none;
}
.activity-item__icon {
  font-size: 18px;
  flex-shrink: 0;
}
.activity-item__main {
  flex: 1;
  min-width: 0;
}
.activity-item__title {
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.activity-item__meta {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.activity-item__dot {
  opacity: 0.5;
}
.activity-item__right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

/* ── Task 11.1：UI 自动化执行统计 ─────────────────────────────── */
.ui-stats-card :deep(.n-card__content) {
  padding-top: 8px;
}

.ui-stats-card__view-toggle {
  display: inline-flex;
  align-items: center;
}

.ui-stats-kpi {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-secondary, rgba(128, 128, 128, 0.04));
  border: 1px solid var(--border-subtle);
  height: 80px;
}
.ui-stats-kpi--rate {
  background: var(--bg-card);
}

/* 通过率 KPI：圆环左 + 大数字右；圆环固定 56px 不再塞 indicator 文本，
   防止小尺寸里文字溢出环线（验收反馈）。 */
.ui-stats-kpi__rate-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.ui-stats-kpi__rate-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  letter-spacing: -0.5px;
}
.ui-stats-kpi__icon {
  font-size: 28px;
  flex-shrink: 0;
}
.ui-stats-kpi__value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.1;
}
.ui-stats-kpi__label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.ui-stats-kpi__sub {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.ui-stats-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ui-stats-section__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.ui-stats-section__empty {
  padding: 20px 0;
}

.ui-stats-confidence-bar {
  display: flex;
  width: 100%;
  height: 16px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(148, 163, 184, 0.18);
}
.ui-stats-confidence-bar__seg {
  height: 100%;
  transition: width 0.6s ease;
}
.ui-stats-confidence-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.ui-stats-confidence-legend__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ui-stats-confidence-legend__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 999px;
}

.ui-stats-top-keys {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ui-stats-top-keys__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
  transition: background var(--duration-fast) var(--easing-standard);
}
.ui-stats-top-keys__row:hover {
  background: var(--bg-hover, rgba(128, 128, 128, 0.06));
}
.ui-stats-top-keys__rank {
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  width: 24px;
  flex-shrink: 0;
}
.ui-stats-top-keys__key {
  flex: 1;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 12px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ui-stats-top-keys__hint {
  margin-top: 6px;
  font-size: 11px;
}

.ui-stats-recent {
  display: flex;
  flex-direction: column;
}
.ui-stats-recent__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 6px;
  border-bottom: 1px solid var(--border-subtle);
  cursor: pointer;
  transition: background var(--duration-fast) var(--easing-standard);
}
.ui-stats-recent__row:last-child {
  border-bottom: none;
}
.ui-stats-recent__row:hover {
  background: var(--bg-hover, rgba(128, 128, 128, 0.06));
}
.ui-stats-recent__icon {
  font-size: 18px;
  flex-shrink: 0;
}
.ui-stats-recent__main {
  flex: 1;
  min-width: 0;
}
.ui-stats-recent__title {
  font-size: 13px;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  font-variant-numeric: tabular-nums;
}
.ui-stats-recent__meta {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ui-stats-recent__rate {
  flex-shrink: 0;
  text-align: right;
  min-width: 48px;
}
.ui-stats-recent__rate-num {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.ui-stats-recent__rate-label {
  font-size: 10px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
</style>
