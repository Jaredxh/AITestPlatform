<template>
  <!-- 执行详情主页。
       Task 10.3 搭框架；Task 10.4 接入数据可信度可视化；Task 10.5 接入媒体回放：
         - DataConfidenceBadge → 用例标题左侧的"🟢/🟡/🟠"徽章（带 tooltip）
         - SynthesizedDataCard → 用例展开后的 🟡 自造数据卡片（含触发步骤号）
         - DataFailureCard     → 用例展开后的 🟠 失败原因卡片
         - ToolCallTimeline    → 每个步骤里的 tool_call 时间线（secret 遮蔽 + synth/failure 高亮）
         - VideoPlayer         → 顶层"媒体回放"卡：自定义时间轴 + 用例章节跳转
         - ScreenshotViewer    → 顶层"媒体回放"卡：所有 step 截图轮播 + 缩略图条
         - SnapshotViewer      → 每个步骤里的 accessibility tree 高亮查看器 -->
  <div class="exec-detail">
    <page-header
      :title="`执行批次 #${shortId}`"
      :subtitle="subtitle"
      icon="i-carbon-machine-learning-model"
      back
      @back="goBack"
    >
      <template #title-extra>
        <n-tag
          v-if="detail"
          :type="overallStatusType"
          :bordered="false"
          size="medium"
        >
          <template #icon>
            <span :class="overallStatusIcon" />
          </template>
          {{ overallStatusLabel }}
        </n-tag>
        <n-tag
          v-if="detail?.mode === 'debug'"
          type="info"
          :bordered="false"
          size="small"
          class="ml-1"
        >
          调试模式
        </n-tag>
      </template>
      <template #extra>
        <n-button
          v-if="detail?.has_video"
          quaternary
          size="small"
          tag="a"
          :href="videoUrl"
          target="_blank"
        >
          <template #icon><span class="i-carbon-video" /></template>
          观看视频
        </n-button>
        <n-button
          v-if="detail?.has_trace"
          quaternary
          size="small"
          tag="a"
          :href="traceUrl"
          target="_blank"
        >
          <template #icon><span class="i-carbon-download" /></template>
          下载 Trace
        </n-button>
        <n-button quaternary size="small" @click="goReplay">
          <template #icon><span class="i-carbon-recording" /></template>
          回放事件流
        </n-button>
        <n-button
          v-if="hasFailures"
          quaternary
          size="small"
          :loading="retrying"
          @click="handleRetryFailed"
        >
          <template #icon><span class="i-carbon-restart" /></template>
          重跑失败用例
        </n-button>
        <n-popconfirm @positive-click="handleRerunAll">
          <template #trigger>
            <n-button type="primary" size="small" :loading="rerunning">
              <template #icon><span class="i-carbon-renew" /></template>
              按本次配置重跑
            </n-button>
          </template>
          将复用本次的物料 / 环境 / LLM 配置开一个新的执行
        </n-popconfirm>
      </template>
    </page-header>

    <n-spin :show="loading && !detail">
      <template v-if="detail">
        <!-- 测试报告（针对本次执行的汇总分析） -->
        <test-report-card :detail="detail" class="mb-3" />

        <!-- 双进度条卡 -->
        <n-card size="small" class="mb-3 exec-detail__rates">
          <n-grid :cols="2" :x-gap="20" responsive="screen">
            <n-gi span="0:2 768:1">
              <div class="exec-detail__rate">
                <div class="exec-detail__rate-head">
                  <span class="exec-detail__rate-title">业务通过率</span>
                  <span class="exec-detail__rate-hint">剔除「数据失败」用例</span>
                </div>
                <div class="exec-detail__rate-row">
                  <!-- businessRate 为 null 时（全部数据失败 / 0 用例）渲染
                       灰色 0% 条 + foot 文案显示 "—"，不再误导成"100% 绿" -->
                  <n-progress
                    type="line"
                    :percentage="businessRate ?? 0"
                    :status="rateStatus(businessRate)"
                    :height="14"
                    :indicator-placement="'inside'"
                  />
                </div>
                <div class="exec-detail__rate-foot">
                  <template v-if="businessRate === null">
                    无可评估业务用例
                    <template v-if="detail.total_cases > 0">
                      ({{ detail.total_cases }} 项均为 🟠 数据失败)
                    </template>
                  </template>
                  <template v-else>
                    通过 {{ detail.passed_cases }} / 可统计 {{ businessDenom }}
                    <template v-if="businessDenom !== detail.total_cases">
                      （已剔除 {{ detail.total_cases - businessDenom }} 项 🟠 数据失败）
                    </template>
                  </template>
                </div>
              </div>
            </n-gi>
            <n-gi span="0:2 768:1">
              <div class="exec-detail__rate">
                <div class="exec-detail__rate-head">
                  <span class="exec-detail__rate-title">执行通过率</span>
                  <span class="exec-detail__rate-hint">含全部用例</span>
                </div>
                <div class="exec-detail__rate-row">
                  <n-progress
                    type="line"
                    :percentage="executionRate ?? 0"
                    :status="rateStatus(executionRate)"
                    :height="14"
                    :indicator-placement="'inside'"
                  />
                </div>
                <div class="exec-detail__rate-foot">
                  <template v-if="executionRate === null">无可评估用例（共 0 项）</template>
                  <template v-else>
                    通过 {{ detail.passed_cases }} / 总数 {{ detail.total_cases }}
                    · 失败 {{ detail.failed_cases }} · 跳过 {{ detail.skipped_cases }}
                  </template>
                </div>
              </div>
            </n-gi>
          </n-grid>
        </n-card>

        <!-- 数据汇总卡片：🟢 / 🟡 / 🟠 -->
        <n-card size="small" class="mb-3">
          <template #header>
            <div class="exec-detail__section-head">
              <span class="i-carbon-data-base" />
              数据可信度汇总
            </div>
          </template>
          <n-grid :cols="4" :x-gap="16" responsive="screen">
            <n-gi span="0:2 768:1">
              <div class="exec-detail__metric exec-detail__metric--ok">
                <div class="exec-detail__metric-icon">🟢</div>
                <div class="exec-detail__metric-text">
                  <div class="exec-detail__metric-num">{{ confidenceCounts.reliable }}</div>
                  <div class="exec-detail__metric-label">数据可信</div>
                </div>
              </div>
            </n-gi>
            <n-gi span="0:2 768:1">
              <div class="exec-detail__metric exec-detail__metric--warn">
                <div class="exec-detail__metric-icon">🟡</div>
                <div class="exec-detail__metric-text">
                  <div class="exec-detail__metric-num">{{ confidenceCounts.synthesized }}</div>
                  <div class="exec-detail__metric-label">含 AI 自造数据</div>
                </div>
              </div>
            </n-gi>
            <n-gi span="0:2 768:1">
              <div class="exec-detail__metric exec-detail__metric--err">
                <div class="exec-detail__metric-icon">🟠</div>
                <div class="exec-detail__metric-text">
                  <div class="exec-detail__metric-num">{{ confidenceCounts.data_failure }}</div>
                  <div class="exec-detail__metric-label">数据失败</div>
                </div>
              </div>
            </n-gi>
            <n-gi span="0:2 768:1">
              <div class="exec-detail__metric">
                <div class="exec-detail__metric-icon">📋</div>
                <div class="exec-detail__metric-text">
                  <div class="exec-detail__metric-num">{{ detail.total_cases }}</div>
                  <div class="exec-detail__metric-label">用例总数</div>
                </div>
              </div>
            </n-gi>
          </n-grid>
        </n-card>

        <!-- 错误条 -->
        <n-alert
          v-if="detail.error_message"
          type="error"
          :title="`执行错误`"
          class="mb-3"
        >
          {{ detail.error_message }}
        </n-alert>

        <!-- 完整执行录像（Task 10.5 / 2026-05 重构）：
             仅放整段视频，**默认折叠**——多用例批量执行时，顶部展示完整录像
             逻辑关联性弱，用户更关心单用例段。截图轮播已下沉到每个用例的
             collapse-item 里（按用例分组）。Trace 下载按钮直接挂顶部 page-header
             里，不再占媒体卡。 -->
        <details
          v-if="detail.has_video"
          ref="overallVideoDetailsRef"
          class="exec-detail__media-overall"
        >
          <summary class="exec-detail__media-overall-summary">
            <span class="i-carbon-video" />
            <strong>完整执行录像</strong>
            <span class="exec-detail__media-overall-hint">
              整段 webm，包含全部 {{ detail.case_results.length }} 条用例的浏览器画面；
              展开后可在时间轴上跳到任一用例段落
            </span>
          </summary>
          <div class="exec-detail__media-overall-body">
            <video-player
              ref="videoPlayerRef"
              :src="videoUrl"
              :cases="videoChapters"
              :execution-started-at="detail.started_at"
              :execution-duration-ms="detail.duration_ms"
            />
          </div>
        </details>

        <!-- 物料快照 -->
        <test-data-snapshot-panel
          :snapshot="detail.test_data_snapshot as any"
          :manual-overrides="manualOverrides"
          :default-expanded="false"
          class="mb-3"
        />

        <!-- 用例列表 -->
        <n-card size="small" :bordered="false">
          <template #header>
            <div class="exec-detail__section-head">
              <span class="i-carbon-list-checked" />
              用例结果
              <span class="text-tertiary text-xs">
                共 {{ detail.case_results.length }} 条
              </span>
            </div>
          </template>
          <template #header-extra>
            <n-button text size="small" @click="expandAll">
              {{ allExpanded ? "全部折叠" : "全部展开" }}
            </n-button>
          </template>

          <app-empty
            v-if="detail.case_results.length === 0"
            size="small"
            icon="i-carbon-list-checked"
            title="尚无用例结果"
            description="执行可能尚未启动 / 派发失败"
          />

          <n-collapse
            v-else
            :expanded-names="expandedNames"
            display-directive="show"
            @update:expanded-names="onCollapseUpdate"
          >
            <n-collapse-item
              v-for="caseRow in sortedCases"
              :key="caseRow.id"
              :name="caseRow.id"
            >
              <template #header>
                <div class="exec-detail__case-head">
                  <span :class="caseStatusIcon(caseRow.status)" />
                  <strong>#{{ caseRow.sort_order + 1 }}</strong>
                  <span class="exec-detail__case-title">
                    {{ titleByCaseId.get(caseRow.testcase_id || "") || caseRow.testcase_id?.slice(0, 8) || "(已删除用例)" }}
                  </span>
                  <data-confidence-badge :value="caseRow.data_confidence" size="small" />
                  <n-tag
                    :type="caseStatusTagType(caseRow.status)"
                    size="tiny"
                    :bordered="false"
                  >
                    {{ caseStatusLabel(caseRow.status) }}
                  </n-tag>
                  <span v-if="caseRow.duration_ms != null" class="exec-detail__case-meta">
                    <span class="i-carbon-time" />{{ formatDuration(caseRow.duration_ms) }}
                  </span>
                  <span v-if="caseRow.tokens_used > 0" class="exec-detail__case-meta">
                    <span class="i-carbon-meter-alt" />{{ caseRow.tokens_used.toLocaleString() }}
                  </span>
                </div>
              </template>

              <div class="exec-detail__case-body">
                <!-- 用例级媒体回放（2026-05 重构）：
                     截图按用例分组、用 ``<details>`` 默认折叠，避免视觉拥挤。
                     "▶ 跳到本用例视频片段"按钮把顶部完整录像 details 展开
                     并 jumpTo 到本用例 started_at 偏移秒，把"完整视频"和
                     "用例结果"的逻辑关联起来——之前用户反馈"批量执行时
                     视频和截图放在报告顶部没有逻辑关联性"。 -->
                <details
                  v-if="caseScreenshotGroup(caseRow.id) || (detail.has_video && hasCaseVideoOffset(caseRow))"
                  class="exec-detail__case-media"
                >
                  <summary class="exec-detail__case-media-summary">
                    <span class="i-carbon-image" />
                    <span class="exec-detail__case-media-title">用例媒体回放</span>
                    <span
                      v-if="caseScreenshotGroup(caseRow.id)"
                      class="exec-detail__case-media-count"
                    >
                      {{ caseScreenshotGroup(caseRow.id)!.steps.length }} 张步骤截图
                    </span>
                    <n-button
                      v-if="detail.has_video && hasCaseVideoOffset(caseRow)"
                      size="tiny"
                      quaternary
                      type="primary"
                      class="ml-2"
                      @click.stop.prevent="jumpVideoToCase(caseRow)"
                    >
                      <template #icon><span class="i-carbon-play" /></template>
                      跳到本用例视频片段
                    </n-button>
                  </summary>
                  <div class="exec-detail__case-media-body">
                    <screenshot-viewer
                      v-if="caseScreenshotGroup(caseRow.id)"
                      :groups="[caseScreenshotGroup(caseRow.id)!]"
                    />
                    <div v-else class="text-tertiary text-xs">
                      该用例没有步骤截图记录；点上面的按钮直接跳到完整录像里
                      本用例对应时间段。
                    </div>
                  </div>
                </details>

                <n-alert
                  v-if="caseRow.error_message"
                  type="error"
                  :show-icon="false"
                  size="small"
                  class="mb-2"
                >
                  {{ caseRow.error_message }}
                </n-alert>

                <synthesized-data-card
                  v-if="caseRow.synthesized_data.length > 0"
                  :items="caseRow.synthesized_data as SynthesizedItem[]"
                  :steps="caseRow.steps as StepLite[]"
                  @step-click="(sn) => scrollToStep(caseRow.id, sn)"
                />

                <data-failure-card
                  v-if="caseRow.data_failures.length > 0"
                  :items="caseRow.data_failures as FailureItem[]"
                  :steps="caseRow.steps as StepLite[]"
                  @step-click="(sn) => scrollToStep(caseRow.id, sn)"
                />

                <!-- 步骤列表 -->
                <div v-if="caseRow.steps.length === 0" class="exec-detail__no-steps">
                  该用例尚无步骤记录
                </div>
                <div v-else class="exec-detail__steps">
                  <div
                    v-for="step in caseRow.steps"
                    :key="step.id"
                    :id="stepDomId(caseRow.id, step.step_number)"
                    class="exec-detail__step"
                    :class="[
                      `exec-detail__step--${step.status}`,
                      highlightedStepKey === stepDomId(caseRow.id, step.step_number)
                        ? 'exec-detail__step--highlight'
                        : '',
                    ]"
                  >
                    <div class="exec-detail__step-head">
                      <n-tag
                        :type="stepStatusTagType(step.status)"
                        size="tiny"
                        :bordered="false"
                      >
                        步骤 {{ step.step_number }}
                      </n-tag>
                      <span class="exec-detail__step-status">
                        {{ stepStatusLabel(step.status) }}
                      </span>
                      <span v-if="step.duration_ms != null" class="exec-detail__step-meta">
                        <span class="i-carbon-time" />{{ formatDuration(step.duration_ms) }}
                      </span>
                      <span v-if="step.tokens_used > 0" class="exec-detail__step-meta">
                        <span class="i-carbon-meter-alt" />{{ step.tokens_used.toLocaleString() }}
                      </span>
                      <span v-if="step.tool_calls.length > 0" class="exec-detail__step-meta">
                        <span class="i-carbon-tool-kit" />{{ step.tool_calls.length }} 次工具调用
                      </span>
                    </div>
                    <p class="exec-detail__step-desc">{{ step.description }}</p>
                    <p v-if="step.expected_result" class="exec-detail__step-expected">
                      <strong>预期：</strong>{{ step.expected_result }}
                    </p>

                    <!-- 失败步骤高亮：错误 + AssertionJudge 失败原因 -->
                    <n-alert
                      v-if="isStepFailed(step.status)"
                      type="warning"
                      :show-icon="false"
                      size="small"
                      class="exec-detail__step-alert"
                    >
                      <div v-if="step.error_message" class="mb-1">
                        <strong>错误：</strong>{{ step.error_message }}
                      </div>
                      <div v-if="step.assertion_passed === false">
                        <strong>断言未通过：</strong>
                        {{ step.assertion_reason || "无判定原因" }}
                        <div
                          v-if="step.assertion_evidence"
                          class="exec-detail__assertion-evidence"
                        >
                          证据：{{ step.assertion_evidence }}
                        </div>
                      </div>
                    </n-alert>

                    <!-- AI reasoning 折叠 -->
                    <details v-if="step.ai_reasoning" class="exec-detail__reasoning">
                      <summary>
                        <span class="i-carbon-thinking" />
                        AI 推理（{{ step.ai_reasoning.length }} 字）
                      </summary>
                      <pre class="exec-detail__reasoning-text">{{ step.ai_reasoning }}</pre>
                    </details>

                    <!-- Tool-call 时间线（Task 10.4） -->
                    <details
                      v-if="step.tool_calls.length > 0"
                      class="exec-detail__tools"
                      :open="defaultOpenTools(step)"
                    >
                      <summary>
                        <span class="i-carbon-tool-kit" />
                        Tool 调用时间线（{{ step.tool_calls.length }} 次）
                      </summary>
                      <tool-call-timeline
                        :tool-calls="step.tool_calls as RawToolCall[]"
                        class="exec-detail__tools-body"
                      />
                    </details>

                    <!-- Accessibility snapshot 查看器（Task 10.5） -->
                    <details
                      v-if="step.snapshot_after || step.snapshot_before"
                      class="exec-detail__tools"
                      :open="isStepFailed(step.status)"
                    >
                      <summary>
                        <span class="i-carbon-tree-view-alt" />
                        页面结构快照（无障碍树）
                      </summary>
                      <div class="exec-detail__tools-body">
                        <snapshot-viewer
                          :snapshot-before="step.snapshot_before"
                          :snapshot-after="step.snapshot_after"
                        />
                      </div>
                    </details>
                  </div>
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>
        </n-card>
      </template>

      <app-empty
        v-else-if="!loading"
        title="找不到执行记录"
        description="可能已被清理；或链接已过期"
        icon="i-carbon-search"
      >
        <template #actions>
          <n-button @click="goBack">返回历史列表</n-button>
        </template>
      </app-empty>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NGi,
  NGrid,
  NPopconfirm,
  NProgress,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import TestDataSnapshotPanel from "@/components/ui-automation/TestDataSnapshotPanel.vue";
import DataConfidenceBadge from "@/components/ui-automation/DataConfidenceBadge.vue";
import SynthesizedDataCard from "@/components/ui-automation/SynthesizedDataCard.vue";
import type {
  SynthesizedItem,
  StepLite,
} from "@/components/ui-automation/SynthesizedDataCard.vue";
import DataFailureCard from "@/components/ui-automation/DataFailureCard.vue";
import type { FailureItem } from "@/components/ui-automation/DataFailureCard.vue";
import ToolCallTimeline from "@/components/ui-automation/ToolCallTimeline.vue";
import type { RawToolCall } from "@/components/ui-automation/ToolCallTimeline.vue";
import VideoPlayer from "@/components/ui-automation/VideoPlayer.vue";
import type { CaseChapterInput } from "@/components/ui-automation/VideoPlayer.vue";
import ScreenshotViewer from "@/components/ui-automation/ScreenshotViewer.vue";
import type {
  ScreenshotCaseInput,
  ScreenshotStepInput,
} from "@/components/ui-automation/ScreenshotViewer.vue";
import SnapshotViewer from "@/components/ui-automation/SnapshotViewer.vue";
import TestReportCard from "@/components/ui-automation/TestReportCard.vue";
import {
  createExecutionApi,
  executionTraceUrl,
  executionVideoUrl,
  getExecutionApi,
  retryFailedExecutionApi,
  type ExecutionDetailResponse,
  type CaseStatus,
  type ExecutionStepResponse,
} from "@/services/uiAutomation";
import { getTestcaseApi } from "@/services/testcases";

const route = useRoute();
const router = useRouter();
const message = useMessage();

const executionId = computed(() => String(route.params.execId ?? ""));
const projectId = computed(() => String(route.params.projectId ?? ""));

const detail = ref<ExecutionDetailResponse | null>(null);
const loading = ref(false);
const retrying = ref(false);
const rerunning = ref(false);

// 顶部完整录像 details + 视频播放器引用：
// "跳到本用例视频片段"按钮要先打开 details 再 jumpTo（details 没展开时
// VideoPlayer 没挂载，jumpTo 拿不到 video 元素）。
const overallVideoDetailsRef = ref<HTMLDetailsElement | null>(null);
const videoPlayerRef = ref<{ jumpTo: (sec: number) => void } | null>(null);

const expandedNames = ref<string[]>([]);
const allExpanded = computed(() =>
  detail.value !== null &&
  expandedNames.value.length === detail.value.case_results.length,
);

// 用例标题不在 case_results 表里，要去 testcase 表逐个拉。短期方案：并发拉，
// 拿到映射在 UI 渲染时查表。短链路：相同 testcase_id 只拉一次。
const titleByCaseId = ref(new Map<string, string>());

async function fetchTitles(testcaseIds: string[]) {
  const unique = Array.from(new Set(testcaseIds.filter(Boolean)));
  await Promise.all(
    unique.map(async (id) => {
      if (titleByCaseId.value.has(id)) return;
      try {
        const res = await getTestcaseApi(id);
        if (res.success) {
          titleByCaseId.value.set(id, res.data.title);
        }
      } catch {
        /* 用例已删；UI 显示 fallback */
      }
    }),
  );
}

async function fetchDetail() {
  if (!executionId.value) return;
  loading.value = true;
  try {
    const res = await getExecutionApi(executionId.value);
    if (res.success) {
      detail.value = res.data;
      // 默认把失败 / 错误 / data_failure 的用例先展开，方便用户直接看问题
      expandedNames.value = res.data.case_results
        .filter(
          (c) =>
            c.status === "failed" ||
            c.status === "error" ||
            c.data_confidence === "data_failure",
        )
        .map((c) => c.id);
      const ids = res.data.case_results
        .map((c) => c.testcase_id)
        .filter((x): x is string => !!x);
      fetchTitles(ids);
    }
  } finally {
    loading.value = false;
  }
}

onMounted(fetchDetail);
watch(() => executionId.value, () => {
  detail.value = null;
  expandedNames.value = [];
  fetchDetail();
});

// ─── 派生数据 ──────────────────────────────────────────────────────

const shortId = computed(() => executionId.value.slice(0, 8));

const subtitle = computed(() => {
  if (!detail.value) return "加载中…";
  const at = detail.value.created_at
    ? new Date(detail.value.created_at).toLocaleString("zh-CN")
    : "—";
  const dur = formatDuration(detail.value.duration_ms);
  return `创建于 ${at} · 共 ${detail.value.total_cases} 条用例 · 耗时 ${dur}`;
});

const STATUS_DISPLAY: Record<
  string,
  { label: string; type: "default" | "info" | "success" | "warning" | "error"; icon: string }
> = {
  pending: { label: "等待中", type: "default", icon: "i-carbon-time" },
  running: { label: "执行中", type: "info", icon: "i-carbon-rocket" },
  completed: { label: "已完成", type: "success", icon: "i-carbon-checkmark-filled" },
  failed: { label: "失败", type: "error", icon: "i-carbon-error" },
  stopped: { label: "已停止", type: "warning", icon: "i-carbon-stop-filled-alt" },
  aborted_budget: { label: "预算超限", type: "warning", icon: "i-carbon-meter-alt" },
};

const overallStatusLabel = computed(
  () => STATUS_DISPLAY[detail.value?.status ?? "pending"]?.label ?? "—",
);
const overallStatusType = computed(
  () => STATUS_DISPLAY[detail.value?.status ?? "pending"]?.type ?? "default",
);
const overallStatusIcon = computed(
  () => STATUS_DISPLAY[detail.value?.status ?? "pending"]?.icon ?? "",
);

const confidenceCounts = computed(() => {
  const c = { reliable: 0, synthesized: 0, data_failure: 0 };
  if (!detail.value) return c;
  for (const cs of detail.value.case_results) {
    if (cs.data_confidence === "reliable") c.reliable++;
    else if (cs.data_confidence === "synthesized") c.synthesized++;
    else if (cs.data_confidence === "data_failure") c.data_failure++;
  }
  return c;
});

const businessDenom = computed(() => {
  if (!detail.value) return 0;
  return Math.max(0, detail.value.total_cases - confidenceCounts.value.data_failure);
});

/**
 * 业务通过率，``null`` 表示"无可评估业务用例"——历史 bug：分母为 0 时硬当
 * 100% 处理，进度条被涂绿色，让"全部数据失败"的执行看起来像"业务全通过"。
 * 现在返回 ``null`` 让 ``rateStatus`` 走 default 灰色。
 */
const businessRate = computed<number | null>(() => {
  if (!detail.value) return null;
  if (businessDenom.value <= 0) return null;
  return Math.round((detail.value.passed_cases / businessDenom.value) * 100);
});

/**
 * 执行通过率，total=0（如启动后立刻 stop）时返回 ``null``——避免被
 * 误读为 0% 红色失败。
 */
const executionRate = computed<number | null>(() => {
  if (!detail.value || detail.value.total_cases === 0) return null;
  return Math.round((detail.value.passed_cases / detail.value.total_cases) * 100);
});

function rateStatus(
  rate: number | null,
): "success" | "warning" | "error" | "info" | "default" {
  if (rate === null) return "default";
  if (rate >= 95) return "success";
  if (rate >= 70) return "info";
  if (rate >= 40) return "warning";
  return "error";
}

const sortedCases = computed(() => {
  if (!detail.value) return [];
  return [...detail.value.case_results].sort((a, b) => a.sort_order - b.sort_order);
});

const hasFailures = computed(() => {
  if (!detail.value) return false;
  return (detail.value.failed_cases || 0) + (detail.value.skipped_cases || 0) > 0;
});

const manualOverrides = computed(() => {
  const snap = detail.value?.config_snapshot as
    | { manual_overrides?: Record<string, unknown> }
    | undefined;
  return snap?.manual_overrides ?? null;
});

// 视频 / Trace URL 优先用后端返回的 nginx 静态路径
// （``/uploads/ui_artifacts/...``），仅当后端没回这个字段时才退回鉴权 API
// 路径作为兜底。原因：``<video src>`` / ``<a href>`` 等 HTML 元素发请求**不
// 会**自动带 Authorization header（axios interceptor 不参与），鉴权 API 路
// 径会 401 → 播放器触发 onerror 显示"视频加载失败"（实际故障，2026-05 修复）。
const videoUrl = computed(
  () => detail.value?.video_url || executionVideoUrl(executionId.value),
);
const traceUrl = computed(
  () => detail.value?.trace_url || executionTraceUrl(executionId.value),
);

// ─── 媒体回放派生数据（Task 10.5） ────────────────────────────────

const videoChapters = computed<CaseChapterInput[]>(() => {
  if (!detail.value) return [];
  return detail.value.case_results.map((c) => ({
    id: c.id,
    title: titleByCaseId.value.get(c.testcase_id || "") ||
      c.testcase_id?.slice(0, 8) ||
      `用例 ${c.sort_order + 1}`,
    sort_order: c.sort_order,
    status: c.status,
    data_confidence: c.data_confidence,
    started_at: c.started_at,
    completed_at: c.completed_at,
    duration_ms: c.duration_ms,
  }));
});

const screenshotGroups = computed<ScreenshotCaseInput[]>(() => {
  if (!detail.value) return [];
  const out: ScreenshotCaseInput[] = [];
  for (const c of detail.value.case_results) {
    const steps: ScreenshotStepInput[] = c.steps
      .filter((s) => !!(s.screenshot_url || s.screenshot_path))
      .map((s) => ({
        step_id: s.id,
        step_number: s.step_number,
        status: s.status,
        // 优先用后端给的 nginx 静态 URL（无需 token，``<img>`` 直接出图）；
        // 兜底用受保护的 ``/api`` 路径，但那条 ``<img>`` 加载会 401。
        url:
          s.screenshot_url ||
          `/api/ui-executions/steps/${s.id}/screenshot`,
      }));
    if (steps.length === 0) continue;
    out.push({
      case_id: c.id,
      case_title: titleByCaseId.value.get(c.testcase_id || "") ||
        c.testcase_id?.slice(0, 8) ||
        `用例 ${c.sort_order + 1}`,
      case_status: c.status,
      sort_order: c.sort_order,
      steps,
    });
  }
  return out;
});

/** case_id → ScreenshotCaseInput 索引，避免每个用例都遍历 screenshotGroups。 */
const screenshotGroupByCaseId = computed(() => {
  const m = new Map<string, ScreenshotCaseInput>();
  for (const g of screenshotGroups.value) m.set(g.case_id, g);
  return m;
});

function caseScreenshotGroup(caseId: string): ScreenshotCaseInput | undefined {
  return screenshotGroupByCaseId.value.get(caseId);
}

interface CaseRowLite {
  started_at: string | null;
  duration_ms: number | null;
}

/** 该用例是否能在视频里被定位到 —— 必须有 ``started_at`` 才能算偏移秒。 */
function hasCaseVideoOffset(caseRow: CaseRowLite): boolean {
  if (!detail.value?.started_at || !caseRow.started_at) return false;
  return true;
}

/** 算用例在录像里的开始秒数（与 VideoPlayer 章节对齐）。 */
function caseVideoOffsetSec(caseRow: CaseRowLite): number {
  if (!detail.value?.started_at || !caseRow.started_at) return 0;
  const baseMs = new Date(detail.value.started_at).getTime();
  const caseMs = new Date(caseRow.started_at).getTime();
  return Math.max(0, (caseMs - baseMs) / 1000);
}

/**
 * 把顶部完整录像 details 展开 → ``nextTick`` 等 VideoPlayer 挂载 → ``jumpTo``。
 * 用 ``open=true`` 而不是 ``toggle()`` 避免重复点收起；滚动到 details 让用户
 * 看到视频在哪。
 */
async function jumpVideoToCase(caseRow: CaseRowLite) {
  const det = overallVideoDetailsRef.value;
  if (det) det.open = true;
  await nextTick();
  const sec = caseVideoOffsetSec(caseRow);
  videoPlayerRef.value?.jumpTo(sec);
  det?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ─── 状态映射 ──────────────────────────────────────────────────────

const CASE_STATUS_META: Record<
  CaseStatus | "pending" | "running",
  { label: string; type: "default" | "info" | "success" | "warning" | "error"; icon: string }
> = {
  pending: { label: "等待", type: "default", icon: "i-carbon-time" },
  running: { label: "执行中", type: "info", icon: "i-carbon-rocket" },
  passed: { label: "通过", type: "success", icon: "i-carbon-checkmark-filled" },
  failed: { label: "失败", type: "error", icon: "i-carbon-error" },
  error: { label: "错误", type: "error", icon: "i-carbon-warning-alt-filled" },
  skipped: { label: "跳过", type: "warning", icon: "i-carbon-arrow-right" },
};

function caseStatusLabel(s: string) {
  return CASE_STATUS_META[s as CaseStatus]?.label ?? s;
}
function caseStatusTagType(s: string) {
  return CASE_STATUS_META[s as CaseStatus]?.type ?? "default";
}
function caseStatusIcon(s: string) {
  return CASE_STATUS_META[s as CaseStatus]?.icon ?? "i-carbon-time";
}

const STEP_STATUS_META: Record<string, { label: string; type: "default" | "info" | "success" | "warning" | "error" }> = {
  pending: { label: "等待", type: "default" },
  running: { label: "执行中", type: "info" },
  passed: { label: "通过", type: "success" },
  failed: { label: "失败", type: "error" },
  blocked_by_security: { label: "被安全拦截", type: "error" },
  skipped: { label: "跳过", type: "warning" },
  paused: { label: "已暂停", type: "warning" },
};

function stepStatusLabel(s: string) {
  return STEP_STATUS_META[s]?.label ?? s;
}
function stepStatusTagType(s: string) {
  return STEP_STATUS_META[s]?.type ?? "default";
}

function isStepFailed(status: string): boolean {
  return status === "failed" || status === "error" || status === "blocked_by_security";
}

// 数据可信度徽章已抽到 DataConfidenceBadge 组件（Task 10.4），此处不再保留 helper。

// ─── 步骤跳转：SynthesizedDataCard / DataFailureCard 的"步骤 N"按钮 ──

const highlightedStepKey = ref<string | null>(null);

function stepDomId(caseId: string, stepNumber: number): string {
  return `step-${caseId}-${stepNumber}`;
}

/**
 * 默认展开 tool-call 时间线的判断：失败 / 含 secret / 含 synth / 含 mark_failure
 * 时直接展开，方便用户一眼看到关键证据；其余步骤折叠，避免上百条 step 时
 * 把页面撑爆。
 */
function defaultOpenTools(step: ExecutionStepResponse): boolean {
  if (isStepFailed(step.status)) return true;
  const calls = (step.tool_calls ?? []) as Array<Record<string, unknown>>;
  return calls.some((c) => {
    const rn = String(c.raw_name ?? c.name ?? "");
    return (
      rn.endsWith("platform_synthesize_data") ||
      rn.endsWith("platform_mark_data_failure") ||
      rn.endsWith("platform_get_secret")
    );
  });
}

function scrollToStep(caseId: string, stepNumber: number) {
  const id = stepDomId(caseId, stepNumber);
  // 用例本身可能仍处于折叠态——先确保展开
  if (!expandedNames.value.includes(caseId)) {
    expandedNames.value = [...expandedNames.value, caseId];
  }
  // 等 DOM 渲染完再 scroll
  requestAnimationFrame(() => {
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    highlightedStepKey.value = id;
    setTimeout(() => {
      if (highlightedStepKey.value === id) highlightedStepKey.value = null;
    }, 1800);
  });
}

// ─── 工具 ──────────────────────────────────────────────────────────

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 100) / 10}s`;
  const min = Math.floor(ms / 60_000);
  const sec = Math.round((ms % 60_000) / 1000);
  return `${min}m ${sec}s`;
}

// ─── 操作 ──────────────────────────────────────────────────────────

function expandAll() {
  if (!detail.value) return;
  if (allExpanded.value) {
    expandedNames.value = [];
  } else {
    expandedNames.value = detail.value.case_results.map((c) => c.id);
  }
}

function onCollapseUpdate(v: string[] | string) {
  expandedNames.value = Array.isArray(v) ? v : [v];
}

function goBack() {
  router.push({
    name: "UIExecutionHistory",
    params: { projectId: projectId.value },
  });
}

function goReplay() {
  router.push({
    name: "UIExecutionMonitor",
    params: { projectId: projectId.value, execId: executionId.value },
    query: { replay: "1" },
  });
}

async function handleRetryFailed() {
  if (!executionId.value) return;
  retrying.value = true;
  try {
    const res = await retryFailedExecutionApi(executionId.value);
    if (res.success) {
      message.success("已派发失败用例重跑，正在跳转监控页…");
      router.push({
        name: "UIExecutionMonitor",
        params: { projectId: projectId.value, execId: res.data.id },
      });
    }
  } catch (err) {
    const msg =
      typeof err === "object" && err !== null && "message" in err
        ? String((err as { message: unknown }).message)
        : "重跑失败";
    message.error(msg);
  } finally {
    retrying.value = false;
  }
}

async function handleRerunAll() {
  if (!detail.value) return;
  const snap = detail.value.config_snapshot as Record<string, unknown> | null;
  if (!snap) {
    message.error("缺少配置快照，无法复用");
    return;
  }
  const testcaseIds = (snap.testcase_ids as string[] | undefined) ?? [];
  if (testcaseIds.length === 0) {
    message.error("配置快照里没有用例 ID 列表，无法重跑");
    return;
  }
  rerunning.value = true;
  try {
    const res = await createExecutionApi(projectId.value, {
      testcase_ids: testcaseIds,
      environment_id: detail.value.environment_id,
      mode: detail.value.mode as "normal" | "debug",
      llm_config_id: (snap.llm_config_id as string | null) ?? null,
      loaded_set_ids: (snap.loaded_set_ids as string[] | undefined) ?? [],
      manual_overrides:
        (snap.manual_overrides as Record<string, unknown> | undefined) ?? {},
      token_budget: (snap.token_budget_override as number | null) ?? null,
      strict_data_mode: !!snap.strict_data_mode,
    });
    if (res.success) {
      message.success("已派发新执行，正在跳转监控页…");
      router.push({
        name: "UIExecutionMonitor",
        params: { projectId: projectId.value, execId: res.data.id },
      });
    }
  } catch (err) {
    const msg =
      typeof err === "object" && err !== null && "message" in err
        ? String((err as { message: unknown }).message)
        : "重跑失败";
    message.error(msg);
  } finally {
    rerunning.value = false;
  }
}
</script>

<style scoped>
.exec-detail {
  display: flex;
  flex-direction: column;
}

.ml-1 {
  margin-left: 4px;
}

.mb-1 {
  margin-bottom: 4px;
}

.mb-2 {
  margin-bottom: 8px;
}

.mb-3 {
  margin-bottom: 12px;
}

.exec-detail__rates {
  border-left: 4px solid var(--brand-primary);
}

.exec-detail__rate {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 0;
}

.exec-detail__rate-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.exec-detail__rate-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.exec-detail__rate-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

.exec-detail__rate-foot {
  font-size: 12px;
  color: var(--text-secondary);
}

.exec-detail__section-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
}

.exec-detail__section-hint {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

/* 媒体回放卡里的二级 details（"步骤截图轮播"折叠器） */
.exec-detail__media-details,
.exec-detail__media-overall,
.exec-detail__case-media {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-page-soft);
}

.exec-detail__media-overall {
  margin-bottom: 12px;
}

.exec-detail__case-media {
  margin-bottom: 12px;
  background: var(--bg-card);
}

.exec-detail__media-details > summary,
.exec-detail__media-overall-summary,
.exec-detail__case-media-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  list-style: none;
}

.exec-detail__media-details > summary::-webkit-details-marker,
.exec-detail__media-overall-summary::-webkit-details-marker,
.exec-detail__case-media-summary::-webkit-details-marker {
  display: none;
}

.exec-detail__media-details > summary::before,
.exec-detail__media-overall-summary::before,
.exec-detail__case-media-summary::before {
  content: "▸";
  font-size: 10px;
  color: var(--text-tertiary);
  transition: transform var(--duration-fast) var(--easing-standard);
}

.exec-detail__media-details[open] > summary::before,
.exec-detail__media-overall[open] > summary::before,
.exec-detail__case-media[open] > summary::before {
  transform: rotate(90deg);
}

.exec-detail__media-details > summary:hover,
.exec-detail__media-overall-summary:hover,
.exec-detail__case-media-summary:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.exec-detail__media-overall-hint,
.exec-detail__case-media-count {
  font-size: 11.5px;
  color: var(--text-tertiary);
  font-weight: 400;
}

.exec-detail__case-media-title {
  flex-shrink: 0;
}

.exec-detail__media-overall-body,
.exec-detail__case-media-body {
  padding: 12px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-subtle);
}

.exec-detail__media-body {
  padding: 12px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-subtle);
}

.exec-detail__metric {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-page-soft);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
}

.exec-detail__metric--ok {
  background: rgba(22, 163, 74, 0.06);
  border-color: rgba(22, 163, 74, 0.2);
}

.exec-detail__metric--warn {
  background: rgba(245, 158, 11, 0.06);
  border-color: rgba(245, 158, 11, 0.2);
}

.exec-detail__metric--err {
  background: rgba(239, 68, 68, 0.06);
  border-color: rgba(239, 68, 68, 0.2);
}

.exec-detail__metric-icon {
  font-size: 28px;
}

.exec-detail__metric-num {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}

.exec-detail__metric-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.exec-detail__case-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
}

.exec-detail__case-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
  margin-right: auto;
  max-width: 480px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.exec-detail__case-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.exec-detail__case-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 0 8px;
}

.exec-detail__no-steps {
  font-size: 13px;
  color: var(--text-tertiary);
  font-style: italic;
  text-align: center;
  padding: 12px 0;
}

.exec-detail__steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.exec-detail__step {
  border: 1px solid var(--border-subtle);
  border-left-width: 3px;
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.exec-detail__step--failed,
.exec-detail__step--error,
.exec-detail__step--blocked_by_security {
  border-left-color: var(--color-error);
  background: rgba(239, 68, 68, 0.03);
}

.exec-detail__step--passed {
  border-left-color: var(--color-success);
}

.exec-detail__step--skipped {
  border-left-color: var(--color-warning);
}

/* SynthesizedDataCard / DataFailureCard 的"步骤 N"按钮跳转后，目标 step
   闪烁高亮 1.8s，便于用户在长列表里定位 */
.exec-detail__step--highlight {
  outline: 2px solid var(--brand-primary);
  outline-offset: -1px;
  animation: exec-detail-highlight 1.8s ease-out;
}

@keyframes exec-detail-highlight {
  0%, 30% {
    background: rgba(99, 102, 241, 0.12);
  }
  100% {
    background: var(--bg-card);
  }
}

.exec-detail__reasoning,
.exec-detail__tools {
  margin-top: 6px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-page-soft);
}

.exec-detail__reasoning > summary,
.exec-detail__tools > summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  border-radius: var(--radius-sm);
  list-style: none;
}

.exec-detail__reasoning > summary::-webkit-details-marker,
.exec-detail__tools > summary::-webkit-details-marker {
  display: none;
}

.exec-detail__reasoning > summary::before,
.exec-detail__tools > summary::before {
  content: "▸";
  font-size: 10px;
  color: var(--text-tertiary);
  transition: transform var(--duration-fast) var(--easing-standard);
}

.exec-detail__reasoning[open] > summary::before,
.exec-detail__tools[open] > summary::before {
  transform: rotate(90deg);
}

.exec-detail__reasoning > summary:hover,
.exec-detail__tools > summary:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.exec-detail__reasoning-text {
  margin: 0;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  background: var(--bg-card);
  border-top: 1px solid var(--border-subtle);
  max-height: 320px;
  overflow: auto;
}

.exec-detail__tools-body {
  padding: 8px 10px;
  background: var(--bg-card);
  border-top: 1px solid var(--border-subtle);
}

.exec-detail__step-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.exec-detail__step-status {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.exec-detail__step-meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.exec-detail__step-desc {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}

.exec-detail__step-expected {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.exec-detail__step-alert {
  margin-top: 4px;
}

.exec-detail__assertion-evidence {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-secondary);
}

.text-tertiary {
  color: var(--text-tertiary);
}

.text-xs {
  font-size: 12px;
}
</style>
