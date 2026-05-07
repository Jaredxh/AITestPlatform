<template>
  <div>
    <page-header
      title="技能使用统计"
      subtitle="按 skill 维度查看 7 / 30 天调用量、成功率、平均 tokens；点击成功率数字查看失败明细"
      icon="i-carbon-analytics"
      back
      @back="goBackToManagement"
    >
      <template #extra>
        <n-button quaternary @click="fetchAll">
          <template #icon><span class="i-carbon-renew" /></template>
          刷新
        </n-button>
      </template>
    </page-header>

    <page-container surface>
      <n-spin :show="loading">
        <n-data-table
          v-if="rows.length > 0"
          :columns="columns"
          :data="rows"
          :row-key="(row: StatRow) => row.skill_id"
          :row-class-name="rowClass"
          :bordered="false"
          size="medium"
        />
        <app-empty
          v-else-if="!loading"
          icon="i-carbon-chart-line"
          title="近期暂无技能使用记录"
          description="在 AI 对话中使用任意技能后即可看到统计数据"
        />
      </n-spin>
    </page-container>

    <!-- 趋势图弹窗 -->
    <n-modal
      v-model:show="trendModalShow"
      preset="card"
      style="width: 720px; max-width: 90vw"
      :title="trendModalTitle"
    >
      <div v-if="trendLoading" class="stats-modal__loading">
        <n-spin size="small" />
        <span>加载趋势中…</span>
      </div>
      <template v-else-if="trendPoints.length > 0">
        <div class="stats-trend">
          <div class="stats-trend__chart">
            <div
              v-for="(p, i) in trendPoints"
              :key="i"
              class="stats-trend__bar"
              :style="{ height: barHeight(p.count) + '%' }"
              :title="`${p.date}: ${p.count}`"
            >
              <span class="stats-trend__bar-value">{{ p.count }}</span>
            </div>
          </div>
          <div class="stats-trend__axis">
            <span v-for="(p, i) in trendPoints" :key="i" class="stats-trend__axis-tick">
              {{ shortDate(p.date) }}
            </span>
          </div>
        </div>
      </template>
      <n-empty v-else description="近 30 天没有调用记录" />
    </n-modal>

    <!-- 失败明细抽屉 -->
    <n-drawer v-model:show="failureDrawerShow" :width="560">
      <n-drawer-content closable :title="failureDrawerTitle">
        <n-spin :show="failureLoading">
          <div v-if="failures.length > 0" class="stats-fail">
            <div
              v-for="f in failures"
              :key="f.id"
              class="stats-fail__item"
            >
              <div class="stats-fail__head">
                <n-tag size="tiny" type="error" :bordered="false">
                  {{ f.outcome }}
                </n-tag>
                <span class="stats-fail__time">{{ formatTime(f.created_at) }}</span>
                <n-button
                  v-if="f.session_id"
                  quaternary
                  size="tiny"
                  type="info"
                  @click="goToSession(f.session_id)"
                >
                  打开对话
                </n-button>
              </div>
              <div class="stats-fail__msg">
                {{ f.error_message || "（无 error_message）" }}
              </div>
              <div class="stats-fail__meta">
                激活原因：{{ f.activation_reason }}
              </div>
            </div>
          </div>
          <n-empty v-else-if="!failureLoading" description="暂无失败记录" />
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NModal,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import type { DataTableColumn } from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import { useProjectStore } from "@/stores/project";
import {
  getSkillFailuresApi,
  getSkillUsageStatsApi,
  getSkillUsageTrendApi,
  listSkillsApi,
  type SkillListItem,
  type SkillUsageFailure,
  type SkillUsageStats,
  type SkillUsageStatsItem,
  type SkillUsageTrendPoint,
} from "@/services/skills";

interface StatRow {
  skill_id: string;
  name: string;
  slug: string;
  source: string;
  d7: SkillUsageStatsItem | null;
  d30: SkillUsageStatsItem | null;
}

const router = useRouter();
const projectStore = useProjectStore();
const message = useMessage();

const projectId = computed(() => projectStore.currentProjectId || "");

const loading = ref(false);
const rows = ref<StatRow[]>([]);

const trendModalShow = ref(false);
const trendModalTitle = ref("");
const trendPoints = ref<SkillUsageTrendPoint[]>([]);
const trendLoading = ref(false);

const failureDrawerShow = ref(false);
const failureDrawerTitle = ref("");
const failures = ref<SkillUsageFailure[]>([]);
const failureLoading = ref(false);

async function fetchAll() {
  if (!projectId.value) {
    rows.value = [];
    return;
  }
  loading.value = true;
  try {
    const [skillsRes, d7Res, d30Res] = await Promise.all([
      listSkillsApi(projectId.value, { page_size: 200 }),
      getSkillUsageStatsApi(projectId.value, 7).catch(() => null),
      getSkillUsageStatsApi(projectId.value, 30).catch(() => null),
    ]);
    if (!skillsRes.success) {
      message.error("获取技能列表失败");
      return;
    }
    const stats7: SkillUsageStats =
      d7Res && d7Res.success ? d7Res.data : {};
    const stats30: SkillUsageStats =
      d30Res && d30Res.success ? d30Res.data : {};
    rows.value = (skillsRes.data.items as SkillListItem[])
      .map<StatRow>((s) => ({
        skill_id: s.id,
        name: s.name,
        slug: s.slug,
        source: s.source,
        d7: stats7[s.id] ?? null,
        d30: stats30[s.id] ?? null,
      }))
      .filter((r) => r.d7 || r.d30)
      .sort((a, b) => (b.d30?.count ?? 0) - (a.d30?.count ?? 0));
  } finally {
    loading.value = false;
  }
}

async function openTrend(row: StatRow) {
  trendModalTitle.value = `${row.name} · 近 30 天日调用量`;
  trendModalShow.value = true;
  trendLoading.value = true;
  try {
    const res = await getSkillUsageTrendApi(projectId.value, 30, row.skill_id);
    if (res.success) {
      trendPoints.value = res.data[row.skill_id] || [];
    }
  } finally {
    trendLoading.value = false;
  }
}

async function openFailures(row: StatRow) {
  failureDrawerTitle.value = `${row.name} · 失败明细`;
  failureDrawerShow.value = true;
  failureLoading.value = true;
  failures.value = [];
  try {
    const res = await getSkillFailuresApi(row.skill_id, 50);
    if (res.success) {
      failures.value = res.data;
    }
  } finally {
    failureLoading.value = false;
  }
}

function rowClass(row: StatRow): string {
  // 7 天有 5 次以上调用且成功率 < 50% 视为"高风险"，整行染红提示用户优化 trigger
  const d7 = row.d7;
  if (d7 && d7.count >= 5 && d7.success_rate < 0.5) {
    return "stats-row--alert";
  }
  return "";
}

function fmtRate(item: SkillUsageStatsItem | null): string {
  if (!item || item.count === 0) return "—";
  return `${(item.success_rate * 100).toFixed(0)}%`;
}

function fmtCount(item: SkillUsageStatsItem | null): string {
  return item ? String(item.count) : "0";
}

function fmtTokens(item: SkillUsageStatsItem | null): string {
  if (!item || item.avg_tokens === 0) return "—";
  return item.avg_tokens.toFixed(0);
}

function formatTime(s: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

function shortDate(s: string): string {
  return s.slice(5);
}

const trendMaxCount = computed(() => {
  let m = 1;
  for (const p of trendPoints.value) {
    if (p.count > m) m = p.count;
  }
  return m;
});

function barHeight(count: number): number {
  return Math.max(4, (count / trendMaxCount.value) * 100);
}

function goToSession(sessionId: string) {
  failureDrawerShow.value = false;
  router.push({ name: "AIChat", query: { session: sessionId } });
}

const columns = computed<DataTableColumn<StatRow>[]>(() => [
  // 名称 + slug 拆成两列 —— 历史 UI 把 ``<strong>name</strong><code>slug</code>``
  // 直接拼一起，当 name 与 slug 仅大小写不同时（如 ``cq-qa-financial-reportCheck``
  // vs ``cq-qa-financial-reportcheck``）视觉上就是同一个字符串重复贴在一起，
  // 用户难以区分。分列后 name 走文本列、slug 走等宽 code 列，一眼可读。
  {
    title: "名称",
    key: "name",
    minWidth: 200,
    ellipsis: { tooltip: true },
    render(row) {
      return h("div", { class: "stats-row__name" }, [
        row.source === "built_in"
          ? h(NTag, { size: "tiny", type: "warning", bordered: false }, () => "内置")
          : null,
        h("span", { class: "stats-row__name-text" }, row.name),
      ]);
    },
  },
  {
    title: "Slug",
    key: "slug",
    minWidth: 220,
    ellipsis: { tooltip: true },
    render(row) {
      return h("code", { class: "stats-row__slug" }, row.slug);
    },
  },
  {
    title: "7 天调用",
    key: "d7c",
    width: 100,
    render(row) {
      return h("span", { class: "stats-row__num" }, fmtCount(row.d7));
    },
  },
  {
    title: "30 天调用",
    key: "d30c",
    width: 110,
    render(row) {
      return h(
        NButton,
        {
          quaternary: true,
          size: "small",
          onClick: () => openTrend(row),
        },
        () => fmtCount(row.d30),
      );
    },
  },
  {
    title: "成功率（7 天）",
    key: "rate",
    width: 130,
    render(row) {
      const txt = fmtRate(row.d7);
      const isLow = !!row.d7 && row.d7.count >= 1 && row.d7.success_rate < 0.5;
      return h(
        NButton,
        {
          quaternary: true,
          size: "small",
          type: isLow ? "error" : "default",
          onClick: () => openFailures(row),
        },
        () => txt,
      );
    },
  },
  {
    title: "平均 tokens（7 天）",
    key: "avg_tokens",
    width: 150,
    render(row) {
      return h("span", { class: "stats-row__num" }, fmtTokens(row.d7));
    },
  },
]);

function goBackToManagement() {
  router.push({ name: "SkillManagement" });
}

watch(() => projectId.value, fetchAll);
onMounted(fetchAll);
</script>

<style scoped>
.stats-row__name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.stats-row__name-text {
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats-row__slug {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-page-soft);
  padding: 2px 6px;
  border-radius: 3px;
  word-break: break-all;
}

.stats-row__num {
  font-variant-numeric: tabular-nums;
}

:deep(.stats-row--alert) td {
  background: rgba(208, 48, 80, 0.08) !important;
}

.stats-modal__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--text-tertiary);
}

.stats-trend {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stats-trend__chart {
  display: flex;
  gap: 4px;
  align-items: flex-end;
  height: 200px;
  padding: 12px;
  background: var(--bg-page-soft);
  border-radius: var(--radius-md);
}

.stats-trend__bar {
  flex: 1;
  background: linear-gradient(180deg, var(--brand-primary), color-mix(in srgb, var(--brand-primary) 60%, transparent));
  border-radius: 4px 4px 0 0;
  position: relative;
  min-height: 2px;
  transition: height 240ms ease;
}

.stats-trend__bar-value {
  position: absolute;
  top: -16px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 10px;
  color: var(--text-secondary);
}

.stats-trend__axis {
  display: flex;
  gap: 4px;
  padding: 0 12px;
}

.stats-trend__axis-tick {
  flex: 1;
  text-align: center;
  font-size: 10px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stats-fail {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stats-fail__item {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  background: var(--bg-card);
}

.stats-fail__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.stats-fail__time {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.stats-fail__msg {
  font-size: 13px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bg-page-soft);
  padding: 6px 8px;
  border-radius: var(--radius-sm);
}

.stats-fail__meta {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 6px;
}
</style>
