<template>
  <div class="exec-history">
    <page-header
      title="UI 自动化 - 执行历史"
      subtitle="按项目维度查看历次执行记录；行点击进入详情，可回放、重跑失败用例"
      icon="i-carbon-recording"
    >
      <template #extra>
        <n-radio-group v-model:value="viewMode" size="small">
          <n-radio-button
            v-for="opt in viewOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </n-radio-button>
        </n-radio-group>
        <n-button
          quaternary
          size="small"
          :loading="loading"
          @click="refresh"
        >
          <template #icon><span class="i-carbon-renew" /></template>
          刷新
        </n-button>
      </template>
    </page-header>

    <n-alert v-if="!projectStore.currentProjectId" type="warning" class="mb-3">
      请先在顶栏选择一个项目，再查看 UI 自动化执行历史。
    </n-alert>

    <template v-else>
      <!-- 双视图说明 -->
      <n-card size="small" class="mb-3 exec-history__legend">
        <div class="exec-history__legend-row">
          <span class="i-carbon-information text-info" />
          <strong>{{ viewMode === "business" ? "业务视图" : "执行视图" }}</strong>
          <span class="text-tertiary">
            {{ viewMode === "business"
              ? "通过率分母自动剔除「数据失败」用例（🟠 data_failure），更贴近被测系统真实质量"
              : "通过率反映自动化执行覆盖率，所有用例都计入分母"
            }}
          </span>
        </div>
      </n-card>

      <!-- 状态过滤 -->
      <n-card size="small" class="mb-3">
        <div class="exec-history__filter">
          <n-radio-group
            v-model:value="statusFilter"
            size="small"
            @update:value="onStatusFilter"
          >
            <n-radio-button value="">全部</n-radio-button>
            <n-radio-button value="running">执行中</n-radio-button>
            <n-radio-button value="completed">已完成</n-radio-button>
            <n-radio-button value="failed">失败</n-radio-button>
            <n-radio-button value="stopped">已停止</n-radio-button>
            <n-radio-button value="aborted_budget">预算超限</n-radio-button>
          </n-radio-group>
          <span class="text-xs text-tertiary">共 {{ total }} 条</span>
        </div>
      </n-card>

      <n-card size="small" :bordered="false">
        <n-spin :show="loading">
          <app-empty
            v-if="!loading && items.length === 0"
            icon="i-carbon-recording"
            title="尚无执行记录"
            description="从「测试用例」页选用例后点击「执行」即可生成第一条执行记录"
          />
          <n-data-table
            v-else
            :columns="columns"
            :data="items"
            :row-key="(r: ExecutionListItem) => r.id"
            :pagination="paginationProps"
            :bordered="false"
            size="small"
            @update:page="onPage"
          />
        </n-spin>
      </n-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NPopconfirm,
  NProgress,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, PaginationProps } from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import { useProjectStore } from "@/stores/project";
import {
  deleteExecutionApi,
  EXECUTION_MODE_META,
  listExecutionsApi,
  type ExecutionListItem,
  type ExecutionStatus,
} from "@/services/uiAutomation";

const projectStore = useProjectStore();
const route = useRoute();
const router = useRouter();
const message = useMessage();

const items = ref<ExecutionListItem[]>([]);
const total = ref(0);
const loading = ref(false);
const page = ref(1);
const pageSize = ref(20);
const statusFilter = ref<string>("");
const viewMode = ref<"business" | "execution">(
  (route.query.view as "business" | "execution") || "business",
);

const viewOptions = [
  { label: "业务视图", value: "business" },
  { label: "执行视图", value: "execution" },
];

// ─── 数据加载 ──────────────────────────────────────────────────────

async function fetchPage() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  loading.value = true;
  try {
    const res = await listExecutionsApi(projectId, {
      page: page.value,
      page_size: pageSize.value,
      status: (statusFilter.value || undefined) as ExecutionStatus | undefined,
    });
    if (res.success) {
      items.value = res.data.items;
      total.value = res.data.total;
    }
  } finally {
    loading.value = false;
  }
}

function refresh() {
  fetchPage();
}

function onPage(p: number) {
  page.value = p;
  fetchPage();
}

function onStatusFilter() {
  page.value = 1;
  fetchPage();
}

// 切换业务/执行视图时把当前选择写到 URL，刷新后保留
watch(viewMode, (v) => {
  router.replace({
    query: { ...route.query, view: v },
  });
});

watch(
  () => projectStore.currentProjectId,
  () => {
    page.value = 1;
    fetchPage();
  },
);

onMounted(() => {
  fetchPage();
});

// ─── 表格列 ────────────────────────────────────────────────────────

const STATUS_META: Record<
  string,
  { label: string; type: "default" | "info" | "success" | "warning" | "error"; icon: string }
> = {
  pending: { label: "等待", type: "default", icon: "i-carbon-time" },
  running: { label: "执行中", type: "info", icon: "i-carbon-rocket" },
  completed: { label: "已完成", type: "success", icon: "i-carbon-checkmark-filled" },
  failed: { label: "失败", type: "error", icon: "i-carbon-error" },
  stopped: { label: "已停止", type: "warning", icon: "i-carbon-stop-filled-alt" },
  aborted_budget: { label: "预算超限", type: "warning", icon: "i-carbon-meter-alt" },
};

function statusTag(status: string) {
  return STATUS_META[status] ?? { label: status, type: "default" as const, icon: "" };
}

/**
 * 业务通过率 = passed / (total - data_failure_cases)
 * 执行通过率 = passed / total
 *
 * 极端：分母为 0（全是 data_failure，或 total 为 0）按 100% 处理，避免 NaN
 * 让进度条变灰；含义是"可统计的用例都通过了"或"无可统计用例"。
 */
function computePassRate(row: ExecutionListItem): number {
  const denom = viewMode.value === "business"
    ? Math.max(0, row.total_cases - (row.data_failure_cases || 0))
    : row.total_cases;
  if (denom <= 0) return row.total_cases === 0 ? 0 : 100;
  return Math.round((row.passed_cases / denom) * 100);
}

function rateColor(rate: number): "success" | "warning" | "error" | "info" {
  if (rate >= 95) return "success";
  if (rate >= 70) return "info";
  if (rate >= 40) return "warning";
  return "error";
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 100) / 10}s`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.round((ms % 60_000) / 1000);
  return `${min}m ${sec}s`;
}

function formatTime(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) {
    return `今天 ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  }
  return d.toLocaleString("zh-CN", { hour12: false });
}

const columns = computed<DataTableColumns<ExecutionListItem>>(() => [
  {
    title: "执行 ID",
    key: "id",
    width: 110,
    fixed: "left",
    render: (row) => h("code", { class: "exec-history__id" }, [`#${row.id.slice(0, 8)}`]),
  },
  {
    title: "状态",
    key: "status",
    width: 110,
    render: (row) => {
      const s = statusTag(row.status);
      return h(
        NTag,
        { type: s.type, size: "small", bordered: false },
        () => s.label,
      );
    },
  },
  {
    title: "模式",
    key: "mode",
    width: 80,
    render: (row) => {
      const m = EXECUTION_MODE_META[row.mode] ?? { label: row.mode, color: "default" };
      return h(
        NTag,
        { type: m.color, size: "tiny", bordered: false },
        () => m.label,
      );
    },
  },
  {
    title: viewMode.value === "business" ? "业务通过率" : "执行通过率",
    key: "pass_rate",
    width: 180,
    render: (row) => {
      const rate = computePassRate(row);
      const denom = viewMode.value === "business"
        ? Math.max(0, row.total_cases - (row.data_failure_cases || 0))
        : row.total_cases;
      return h("div", { class: "exec-history__rate-cell" }, [
        h(NProgress, {
          type: "line",
          percentage: rate,
          status: rateColor(rate),
          height: 6,
          showIndicator: false,
        }),
        h("span", { class: "exec-history__rate-text" }, [
          `${row.passed_cases}/${denom}`,
          row.total_cases !== denom
            ? h("span", { class: "exec-history__rate-hint" }, [
                ` (剔除 ${row.total_cases - denom} 项数据失败)`,
              ])
            : null,
        ]),
      ]);
    },
  },
  {
    title: "通过/失败/跳过",
    key: "counts",
    width: 130,
    render: (row) =>
      h("span", null, [
        h("span", { class: "text-success font-bold" }, [String(row.passed_cases)]),
        " / ",
        h("span", { class: "text-error font-bold" }, [String(row.failed_cases)]),
        " / ",
        h("span", { class: "text-tertiary font-bold" }, [String(row.skipped_cases)]),
      ]),
  },
  {
    title: () =>
      h(
        NTooltip,
        { trigger: "hover", placement: "top" },
        {
          trigger: () =>
            h("span", { class: "exec-history__col-title" }, [
              "数据可信度 ",
              h("span", {
                class: "i-carbon-information exec-history__col-info",
              }),
            ]),
          default: () =>
            h("div", { class: "exec-history__tip" }, [
              h("div", null, "按用例聚合本次执行的测试数据来源："),
              h("div", null, "🟢 数据可信 — 全部数据都来自物料集，无 AI 自造"),
              h("div", null, "🟡 含 AI 自造 — 部分数据由 AI 兜底生成"),
              h("div", null, "🟠 数据失败 — 缺料且 AI 也造不出，用例被提前终止"),
            ]),
        },
      ),
    key: "confidence",
    width: 180,
    render: (row) => {
      const r = row.reliable_cases || 0;
      const s = row.synthesized_cases || 0;
      const f = row.data_failure_cases || 0;
      if (r + s + f === 0) {
        return h("span", { class: "text-tertiary text-xs" }, "—");
      }
      const chip = (tipText: string, cls: string, emoji: string, n: number) =>
        h(
          NTooltip,
          { trigger: "hover", placement: "top" },
          {
            trigger: () =>
              h(
                "span",
                { class: `exec-history__conf-chip exec-history__conf-chip--${cls}` },
                [`${emoji} ${n}`],
              ),
            default: () => tipText,
          },
        );
      return h("span", { class: "exec-history__confidence" }, [
        chip("数据可信 — 全部数据来自物料集，无需 AI 自造", "ok", "🟢", r),
        chip("含 AI 自造 — AI 兜底造了部分数据，需人工复核", "warn", "🟡", s),
        chip("数据失败 — 缺料且 AI 也造不出，用例被终止", "err", "🟠", f),
      ]);
    },
  },
  {
    title: "耗时",
    key: "duration_ms",
    width: 90,
    render: (row) => formatDuration(row.duration_ms),
  },
  {
    title: "Tokens",
    key: "tokens_total",
    width: 90,
    render: (row) => row.tokens_total.toLocaleString(),
  },
  {
    title: "触发时间",
    key: "created_at",
    width: 160,
    render: (row) => formatTime(row.created_at),
  },
  {
    title: "操作",
    key: "actions",
    width: 280,
    fixed: "right",
    align: "center",
    titleAlign: "center",
    render: (row) => {
      const terminal = isTerminal(row.status);
      return h("div", { class: "exec-history__actions" }, [
        h(
          NButton,
          {
            size: "tiny",
            type: "primary",
            text: true,
            onClick: () => goToDetail(row),
          },
          () => [h("span", { class: "i-carbon-report mr-1" }), "执行报告"],
        ),
        h(
          NButton,
          {
            size: "tiny",
            text: true,
            disabled: !isRunning(row.status),
            onClick: () => goToMonitor(row),
          },
          () => [h("span", { class: "i-carbon-rocket mr-1" }), "监控"],
        ),
        // 删除按钮：必须终态才能点；非终态 disable + tooltip 提示先停止
        // ``n-popconfirm`` 做二次确认（不可恢复操作）。三个按钮间通过 CSS
        // gap=18px 视觉分隔，避免误点。
        h(
          NPopconfirm,
          {
            placement: "top-end",
            negativeText: "取消",
            positiveText: "确认删除",
            onPositiveClick: () => handleDelete(row),
          },
          {
            trigger: () =>
              h(
                NTooltip,
                { trigger: "hover", placement: "top", disabled: terminal },
                {
                  trigger: () =>
                    h(
                      NButton,
                      {
                        size: "tiny",
                        type: "error",
                        text: true,
                        disabled: !terminal,
                      },
                      () => [
                        h("span", { class: "i-carbon-trash-can mr-1" }),
                        "删除",
                      ],
                    ),
                  default: () => "执行未结束，请先点「监控」页面停止后再删除",
                },
              ),
            default: () =>
              h("div", { class: "exec-history__delete-tip" }, [
                h("div", { class: "font-bold mb-1" }, "删除该执行记录？"),
                h("div", { class: "text-xs text-tertiary" }, [
                  "DB 行 + 关联视频/Trace/截图文件将",
                  h("strong", { class: "text-error" }, "全部删除且不可恢复"),
                  "。",
                ]),
              ]),
          },
        ),
      ]);
    },
  },
]);

function isTerminal(status: string): boolean {
  return ["completed", "stopped", "failed", "aborted_budget"].includes(status);
}

async function handleDelete(row: ExecutionListItem) {
  try {
    const res = await deleteExecutionApi(row.id);
    if (res.success) {
      message.success(
        res.data.files_deleted > 0
          ? `已删除执行 #${row.id.slice(0, 8)}（含 ${res.data.files_deleted} 个 artifact 文件）`
          : `已删除执行 #${row.id.slice(0, 8)}`,
      );
      // 当前页若被删空，回到上一页（或第一页）
      if (items.value.length === 1 && page.value > 1) {
        page.value -= 1;
      }
      await fetchPage();
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "删除失败");
  }
}

function isRunning(status: string): boolean {
  return status === "running" || status === "pending";
}

function goToDetail(row: ExecutionListItem) {
  router.push({
    name: "UIExecutionDetail",
    params: {
      projectId: projectStore.currentProjectId || row.project_id,
      execId: row.id,
    },
  });
}

function goToMonitor(row: ExecutionListItem) {
  router.push({
    name: "UIExecutionMonitor",
    params: {
      projectId: projectStore.currentProjectId || row.project_id,
      execId: row.id,
    },
  });
}

const paginationProps = computed<false | PaginationProps>(() => {
  if (total.value <= pageSize.value && page.value === 1) return false;
  return {
    page: page.value,
    pageSize: pageSize.value,
    itemCount: total.value,
    pageSlot: 7,
  };
});
</script>

<style scoped>
.exec-history {
  display: flex;
  flex-direction: column;
}

.exec-history__legend {
  border-left: 3px solid var(--color-info);
}

.exec-history__legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.exec-history__filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.exec-history__id {
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

.exec-history__rate-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.exec-history__rate-text {
  font-size: 11px;
  color: var(--text-secondary);
}

.exec-history__rate-hint {
  color: var(--text-tertiary);
  font-style: italic;
}

.exec-history__confidence {
  display: inline-flex;
  gap: 4px;
  flex-wrap: wrap;
}

.exec-history__conf-chip {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 600;
}

.exec-history__conf-chip--ok {
  background: rgba(22, 163, 74, 0.12);
  color: #15803d;
}

.exec-history__conf-chip--warn {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.exec-history__conf-chip--err {
  background: rgba(239, 68, 68, 0.16);
  color: #b91c1c;
}

.exec-history__actions {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 三个文本按钮之间留宽松间距，避免「执行报告 监控 删除」挤在一起误点 */
  gap: 18px;
  width: 100%;
}

.exec-history__actions :deep(.n-button) {
  white-space: nowrap;
  /* 让按钮内 icon 与文字之间也有 4px 视觉缝，整体更通透 */
  --n-padding: 0 4px;
}

.exec-history__delete-tip {
  max-width: 240px;
  font-size: 12px;
  line-height: 1.55;
}

.exec-history__col-title {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  cursor: help;
}

.exec-history__col-info {
  font-size: 14px;
  color: var(--text-tertiary);
  opacity: 0.8;
}

.exec-history__tip {
  line-height: 1.7;
  font-size: 12px;
  min-width: 300px;
}

.text-success {
  color: var(--color-success, #16a34a);
}
.text-error {
  color: var(--color-error, #ef4444);
}
.text-warning {
  color: var(--color-warning, #f59e0b);
}
.text-tertiary {
  color: var(--text-tertiary);
}
.text-info {
  color: var(--color-info, #0ea5e9);
}
.text-xs {
  font-size: 12px;
}
.font-bold {
  font-weight: 700;
}
.mb-3 {
  margin-bottom: 12px;
}
</style>
