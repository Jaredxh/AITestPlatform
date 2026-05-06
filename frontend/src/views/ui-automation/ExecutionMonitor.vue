<template>
  <!-- 执行监控页（Task 10.2，替换 Task 10.1 占位实现）。
       页面框架：
       1. PageHeader：执行 ID + 项目 + 模式徽章 + 停止按钮 + 调试模式继续按钮
       2. 概览卡：状态 / 通过-失败-跳过 / 耗时 / tokens（实时随事件更新）
       3. 缺料黄横幅（missing_data_warning）；strict 模式下变红
       4. Token 预算进度条 + 80% 预警
       5. 调试模式 step_paused 提示条 + "继续下一步" CTA
       6. 用例列表（CaseProgress + StepDetail）实时更新
       7. 全局事件时间线（折叠，默认隐藏）—— 调试用 -->
  <div class="exec-monitor">
    <page-header
      :title="`执行监控 #${shortId}`"
      :subtitle="subtitle"
      icon="i-carbon-machine-learning-model"
    >
      <template #extra>
        <n-tag
          v-if="meta.mode === 'debug'"
          type="info"
          :bordered="false"
          size="small"
          class="mr-2"
        >
          <template #icon><span class="i-carbon-debug" /></template>
          调试模式
        </n-tag>
        <n-tag
          v-if="meta.is_replay"
          type="warning"
          :bordered="false"
          size="small"
          class="mr-2"
        >
          <template #icon><span class="i-carbon-recording" /></template>
          回放
        </n-tag>
        <n-button
          v-if="liveViewAvailable"
          :type="showLiveView ? 'primary' : 'info'"
          ghost
          size="small"
          @click="toggleLiveView"
        >
          <template #icon>
            <span :class="showLiveView ? 'i-carbon-close' : 'i-carbon-screen'" />
          </template>
          {{ showLiveView ? "关闭实时画面" : "实时画面" }}
        </n-button>
        <n-button
          v-if="canStop"
          type="error"
          ghost
          size="small"
          :loading="stopping"
          @click="handleStop"
        >
          <template #icon><span class="i-carbon-stop-filled-alt" /></template>
          停止执行
        </n-button>
        <n-button quaternary size="small" @click="goDetail">
          <template #icon><span class="i-carbon-document" /></template>
          查看详情
        </n-button>
        <n-button quaternary size="small" @click="goBack">
          <template #icon><span class="i-carbon-arrow-left" /></template>
          返回执行历史
        </n-button>
      </template>
    </page-header>

    <!-- 顶层错误条：连接错误或后端 error 事件 -->
    <n-alert v-if="bannerError" type="error" class="mb-3" closable>
      {{ bannerError }}
    </n-alert>

    <!-- 顶层提示条：headless 降级 / 预算预警等非致命警告。复用 budgetWarning。 -->
    <n-alert
      v-if="budgetWarning && !bannerError"
      type="warning"
      class="mb-3"
      closable
    >
      {{ budgetWarning }}
    </n-alert>

    <n-alert v-if="meta.is_replay_only" type="info" class="mb-3" :show-icon="false">
      该执行已结束且实时流已被释放，下方仅展示终态信息；点击"查看详情"获取完整步骤数据。
    </n-alert>

    <!-- 概览卡 -->
    <n-card size="small" class="mb-3 exec-monitor__overview">
      <n-grid :cols="6" :x-gap="16" responsive="screen">
        <n-gi span="0:6 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">状态</span>
            <n-tag
              :type="overallTagType"
              :bordered="false"
              size="medium"
            >
              <template #icon>
                <span :class="overallIcon" />
              </template>
              {{ overallLabel }}
            </n-tag>
          </div>
        </n-gi>
        <n-gi span="0:3 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">通过</span>
            <span class="exec-monitor__stat-value text-success">{{ meta.passed }}</span>
            <span class="exec-monitor__stat-sub">/ {{ meta.total }}</span>
          </div>
        </n-gi>
        <n-gi span="0:3 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">失败</span>
            <span class="exec-monitor__stat-value text-error">{{ meta.failed }}</span>
          </div>
        </n-gi>
        <n-gi span="0:3 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">跳过</span>
            <span class="exec-monitor__stat-value text-tertiary">{{ meta.skipped }}</span>
          </div>
        </n-gi>
        <n-gi span="0:3 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">耗时</span>
            <span class="exec-monitor__stat-value">{{ durationText }}</span>
          </div>
        </n-gi>
        <n-gi span="0:3 768:1">
          <div class="exec-monitor__stat">
            <span class="exec-monitor__stat-label">Tokens</span>
            <span class="exec-monitor__stat-value">{{ tokensText }}</span>
          </div>
        </n-gi>
      </n-grid>

      <!-- 进度条：用例完成度 -->
      <div class="exec-monitor__progress">
        <n-progress
          type="line"
          :percentage="caseCompletion"
          :status="progressStatus"
          :height="8"
          :show-indicator="false"
        />
        <span class="exec-monitor__progress-label">
          已完成 {{ completedCases }} / {{ meta.total }} 条用例
        </span>
      </div>

      <!-- Token 预算进度条 + 80% 预警 -->
      <div v-if="tokenBudget > 0" class="exec-monitor__budget">
        <div class="exec-monitor__budget-row">
          <span class="text-xs text-tertiary">Token 预算</span>
          <span class="text-xs">
            {{ tokenUsed.toLocaleString() }} / {{ tokenBudget.toLocaleString() }}
            <strong v-if="tokenOver80" class="text-warning">
              · {{ Math.round((tokenUsed / tokenBudget) * 100) }}%
            </strong>
          </span>
        </div>
        <n-progress
          type="line"
          :percentage="tokenPct"
          :status="tokenStatus"
          :height="6"
          :show-indicator="false"
        />
        <div v-if="budgetWarning" class="text-xs text-warning mt-1">
          ⚠ {{ budgetWarning }}
        </div>
      </div>
    </n-card>

    <!-- 缺料告警条 -->
    <missing-data-banner
      v-if="missingBundle"
      :missing-keys="missingBundle.alerts.map((a) => a.key)"
      :details="missingBundle.alerts"
      :strict-mode="missingBundle.strict"
      class="mb-3"
    />

    <!-- 调试模式：step_paused → 继续按钮 -->
    <n-alert
      v-if="pausedStep"
      type="info"
      :show-icon="false"
      class="mb-3 exec-monitor__pause"
    >
      <template #header>
        <span class="exec-monitor__pause-title">
          <span class="i-carbon-pause-filled" />
          调试模式：步骤 {{ pausedStep.step_number }} 已暂停，等待继续
        </span>
      </template>
      <div class="exec-monitor__pause-body">
        <span class="text-secondary">
          {{ pausedStep.timeout_seconds
            ? `${pausedStep.timeout_seconds}s 内无操作将自动停止`
            : "请确认页面状态后点击继续" }}
        </span>
        <n-button
          type="primary"
          :loading="continuing"
          @click="handleContinue"
        >
          <template #icon><span class="i-carbon-skip-forward" /></template>
          继续下一步
        </n-button>
      </div>
    </n-alert>

    <!-- 用例列表 -->
    <n-card size="small" :bordered="false" class="exec-monitor__cases">
      <template #header>
        <div class="exec-monitor__cases-head">
          <span class="i-carbon-list-checked" />
          <span>用例执行进度</span>
        </div>
      </template>
      <template #header-extra>
        <n-button text size="small" @click="showTimeline = !showTimeline">
          <template #icon>
            <span :class="showTimeline ? 'i-carbon-list' : 'i-carbon-list-boxes'" />
          </template>
          {{ showTimeline ? "返回用例视图" : "查看事件流" }}
        </n-button>
      </template>

      <div v-if="!showTimeline">
        <n-empty
          v-if="cases.length === 0"
          :description="emptyDescription"
        />
        <case-progress
          v-for="c in cases"
          :key="c.case_result_id"
          :case-item="c"
        />
      </div>

      <div v-else class="exec-monitor__timeline-wrap">
        <n-empty v-if="timeline.length === 0" description="暂无事件" />
        <n-timeline v-else>
          <n-timeline-item
            v-for="t in timelineDisplay"
            :key="t.id"
            :type="timelineLevelType(t.level)"
            :title="t.message"
            :time="formatTimelineTime(t.timestamp)"
          >
            <span class="text-xs text-tertiary">{{ t.type }}</span>
          </n-timeline-item>
        </n-timeline>
      </div>
    </n-card>

    <!-- 有头浏览器实时画面（noVNC iframe）─────────────────────────────
         设计要点：
         - 抽屉宽度 ≥ 1024，承载 1920×1080 chromium 缩放显示效果较好；屏小退到全屏
         - placement=right 让用户能边看画面边盯监控页其它信息（用例进度 / 时间线）
         - iframe sandbox 故意宽松：noVNC 内部需要 allow-scripts/forms/popups 等才能
           处理键盘事件 + 上传剪贴板；同源策略下 SAMEORIGIN 已经把外站隔离了
         - :key 强制重连：每次打开都新建 iframe（v-if 控制），关闭即销毁不持续占用 ws -->
    <n-drawer
      v-model:show="showLiveView"
      :width="liveViewDrawerWidth"
      placement="right"
      :auto-focus="false"
      class="exec-monitor__live-drawer"
    >
      <!--
        实时画面抽屉：用 :deep() 强制覆盖 NaiveUI 内部多层容器的高度链。
        body-content-style 只能改最内层 wrapper，对中间套的 n-scrollbar /
        n-drawer-body 都不可见，这就是上一版"高度仍然只有顶部"的原因。
      -->
      <n-drawer-content closable :native-scrollbar="true">
        <template #header>
          <span class="exec-monitor__live-head">
            <span class="i-carbon-screen" />
            实时画面（noVNC · DISPLAY=:99）
            <span class="exec-monitor__live-hint text-xs text-tertiary">
              · 键鼠在画面里直接交互；点击页头按钮或右上 × 关闭
            </span>
          </span>
        </template>
        <div class="exec-monitor__live-body">
          <iframe
            v-if="showLiveView"
            :key="liveViewIframeKey"
            :src="liveViewIframeSrc"
            class="exec-monitor__live-iframe"
            allow="clipboard-read; clipboard-write"
            referrerpolicy="same-origin"
          />
        </div>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NCard,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NGi,
  NGrid,
  NProgress,
  NTag,
  NTimeline,
  NTimelineItem,
  useMessage,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import MissingDataBanner from "@/components/ui-automation/MissingDataBanner.vue";
import CaseProgress from "@/components/ui-automation/CaseProgress.vue";
import { useExecutionSSE } from "@/composables/useExecutionSSE";
import {
  continueExecutionApi,
  getExecutionApi,
  getLiveViewStatusApi,
  stopExecutionApi,
  type ExecutionDetailResponse,
  type ExecutionStatus,
  type LiveViewStatus,
} from "@/services/uiAutomation";

const route = useRoute();
const router = useRouter();
const message = useMessage();

const executionId = computed(() => String(route.params.execId ?? ""));
const projectId = computed(() => String(route.params.projectId ?? ""));

const stopping = ref(false);
const continuing = ref(false);
const showTimeline = ref(false);

// ── 有头浏览器实时画面（noVNC）──────────────────────────────────────
// 仅当 ① 后端 ``UI_NOVNC_ENABLED=true`` ② websockify 端口实际监听 ③ 当前执行
// 仍在跑（status 非终态）时展示按钮。终态用例查看请走视频回放路径，VNC 流
// 是实时的，看一个已结束的执行没有意义。
const liveView = ref<LiveViewStatus | null>(null);
const showLiveView = ref(false);
const liveViewIframeKey = ref(0); // 抽屉关闭后重新打开 → 强制重连

const TERMINAL: ExecutionStatus[] = [
  "completed",
  "stopped",
  "failed",
  "aborted_budget",
];

// 是否回放模式（Task 10.3 入口：详情页 → "回放事件流"按钮 → ?replay=1）
const isReplayMode = computed(() => route.query.replay === "1");

// SSE composable —— url 用 getter 形式，便于"实时 ↔ 回放"切换且 watcher 重启
const sse = useExecutionSSE({
  url: () => {
    const base = `/api/ui-executions/${executionId.value}`;
    return isReplayMode.value ? `${base}/replay` : `${base}/stream`;
  },
  isReplay: isReplayMode.value,
  initialTokenBudget: 50_000,
});
const meta = sse.meta;
const cases = sse.cases;
const missingBundle = sse.missing;
const tokenBudget = sse.tokenBudget;
const tokenUsed = sse.tokenUsed;
const tokenOver80 = sse.tokenOver80;
const budgetWarning = sse.budgetWarning;
const pausedStep = sse.pausedStep;
const timeline = sse.timeline;

// 兜底：执行启动前先 GET 一次详情，拿到 mode / token_budget 真实值，并预填
// 计数；这样 SSE 还没连上时页面也不空白。
const initialDetail = ref<ExecutionDetailResponse | null>(null);

async function fetchInitial() {
  if (!executionId.value) return;
  try {
    const res = await getExecutionApi(executionId.value);
    if (res.success) {
      initialDetail.value = res.data;
      meta.value.execution_id = res.data.id;
      meta.value.total = res.data.total_cases;
      meta.value.passed = res.data.passed_cases;
      meta.value.failed = res.data.failed_cases;
      meta.value.skipped = res.data.skipped_cases;
      meta.value.duration_ms = res.data.duration_ms;
      meta.value.tokens_total = res.data.tokens_total;
      meta.value.mode = res.data.mode;
      tokenUsed.value = res.data.tokens_total;
      // 后端返回的 effective_token_budget 已在 service 层做好
      // `override > environment > 默认` 的 fallback；前端照用即可
      if (
        typeof res.data.effective_token_budget === "number" &&
        res.data.effective_token_budget > 0
      ) {
        tokenBudget.value = res.data.effective_token_budget;
      }
      // 终态执行：把 case_results 预填进监控页面，避免 SSE done 后空白。
      //
      // **必须**走 ``sse.seedCases()`` 而不是 ``cases.value.push()``！
      // 否则 ``replay`` 模式下 SSE 又从头发 ``case_started`` 事件，会再创建
      // 一份重复 case 卡片（实际故障：回放页下方"用例进度"区出现两条同名同 ID
      // 的卡片）。``seedCases`` 把每条 case 注册到 composable 内部
      // ``_caseIdx``，让后续事件命中已有 case 而不是重复 push。
      if (TERMINAL.includes(res.data.status)) {
        meta.value.status = res.data.status;
        sse.seedCases(
          res.data.case_results.map((c) => ({
            case_result_id: c.id,
            testcase_id: c.testcase_id,
            // 详情接口里 ``testcase_no`` / ``testcase_module_name`` 已由
            // ``execution_service._to_case_response`` join testcases / modules
            // 表填上；前端用以渲染 ``TC-0061 标题`` 形式。
            testcase_no: c.testcase_no ?? null,
            testcase_module_name: c.testcase_module_name ?? null,
            title: c.testcase_title ?? "",
            sort_order: c.sort_order,
            status: c.status as never,
            data_confidence: c.data_confidence as never,
            duration_ms: c.duration_ms ?? undefined,
            tokens_used: c.tokens_used,
            error_message: c.error_message,
            steps: c.steps.map((s) => ({
              step_number: s.step_number,
              status: s.status as never,
              description: s.description,
              duration_ms: s.duration_ms ?? undefined,
              tokens_used: s.tokens_used,
              tool_calls_count: s.tool_calls?.length ?? 0,
              tool_calls: (s.tool_calls as never) ?? undefined,
              reasoning: s.ai_reasoning ?? undefined,
              snapshot_after: s.snapshot_after ?? undefined,
              // 后端给的 screenshot_url 已经是 ``/uploads/ui_artifacts/...``
              // 走 nginx 静态资源（无需 Authorization 头），<img> 直接可用。
              // 老数据可能没有 screenshot_url，保留 undefined 让前端展示"无截图"占位。
              screenshot_url: s.screenshot_url ?? undefined,
              error: s.error_message ?? undefined,
              assertion: {
                passed: s.assertion_passed,
                reason: s.assertion_reason,
                evidence: s.assertion_evidence,
              },
            })),
            synthesized: (c.synthesized_data as never[]).map((x) => x as never),
            failures: (c.data_failures as never[]).map((x) => x as never),
          })),
        );
      }
    }
  } catch {
    /* SSE 兜底：详情拉不到也能让 SSE 跑 */
  }
}

const shortId = computed(() => executionId.value.slice(0, 8));

const subtitle = computed(() => {
  if (initialDetail.value?.created_at) {
    return `项目 ${projectId.value.slice(0, 8)} · 触发于 ${formatTime(initialDetail.value.created_at)}`;
  }
  return "正在订阅实时事件…";
});

const STATUS_DISPLAY: Record<
  string,
  { label: string; type: "default" | "info" | "success" | "warning" | "error"; icon: string }
> = {
  connecting: { label: "连接中", type: "default", icon: "i-carbon-link" },
  pending: { label: "等待中", type: "default", icon: "i-carbon-time" },
  streaming: { label: "执行中", type: "info", icon: "i-carbon-rocket" },
  running: { label: "执行中", type: "info", icon: "i-carbon-rocket" },
  completed: { label: "已完成", type: "success", icon: "i-carbon-checkmark-filled" },
  failed: { label: "失败", type: "error", icon: "i-carbon-error" },
  stopped: { label: "已停止", type: "warning", icon: "i-carbon-stop-filled-alt" },
  aborted_budget: { label: "预算超限", type: "warning", icon: "i-carbon-meter-alt" },
  replay_only: { label: "已结束", type: "default", icon: "i-carbon-recording" },
};

const overallLabel = computed(
  () => STATUS_DISPLAY[meta.value.status]?.label ?? meta.value.status,
);
const overallTagType = computed(
  () => STATUS_DISPLAY[meta.value.status]?.type ?? "default",
);
const overallIcon = computed(
  () => STATUS_DISPLAY[meta.value.status]?.icon ?? "i-carbon-time",
);

const canStop = computed(() => {
  const st = meta.value.status;
  return st === "streaming" || st === "running" || st === "pending" || st === "connecting";
});

const completedCases = computed(() => meta.value.passed + meta.value.failed + meta.value.skipped);

const caseCompletion = computed(() => {
  if (meta.value.total === 0) return 0;
  return Math.min(100, Math.round((completedCases.value / meta.value.total) * 100));
});

const progressStatus = computed<"default" | "info" | "success" | "warning" | "error">(() => {
  if (meta.value.status === "completed") return "success";
  if (meta.value.status === "failed" || meta.value.status === "aborted_budget") return "error";
  if (meta.value.status === "stopped") return "warning";
  return "info";
});

const tokenPct = computed(() => {
  if (tokenBudget.value <= 0) return 0;
  return Math.min(100, Math.round((tokenUsed.value / tokenBudget.value) * 100));
});

const tokenStatus = computed<"default" | "info" | "success" | "warning" | "error">(() => {
  if (meta.value.status === "aborted_budget") return "error";
  if (tokenPct.value >= 100) return "error";
  if (tokenPct.value >= 80) return "warning";
  return "info";
});

const durationText = computed(() => {
  if (meta.value.duration_ms == null) return "—";
  const sec = Math.round(meta.value.duration_ms / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
});

const tokensText = computed(() => {
  return tokenUsed.value.toLocaleString();
});

const bannerError = computed(() => {
  if (sse.error.value) return sse.error.value;
  if (meta.value.error_message) return meta.value.error_message;
  return null;
});

const emptyDescription = computed(() => {
  if (meta.value.status === "connecting") return "正在连接实时流…";
  if (meta.value.status === "streaming") return "等待第一条用例事件…";
  return "尚无用例结果";
});

// timeline 倒序显示，最新在最上
const timelineDisplay = computed(() => [...timeline.value].slice().reverse());

function formatTime(s: string): string {
  return new Date(s).toLocaleString("zh-CN");
}

function formatTimelineTime(ts: number): string {
  const d = new Date(ts);
  return `${d.getHours().toString().padStart(2, "0")}:${d
    .getMinutes()
    .toString()
    .padStart(2, "0")}:${d.getSeconds().toString().padStart(2, "0")}`;
}

function timelineLevelType(
  level: "info" | "warning" | "error" | "success",
): "default" | "info" | "warning" | "error" | "success" {
  return level;
}

async function handleStop() {
  if (!executionId.value) return;
  stopping.value = true;
  try {
    const res = await stopExecutionApi(executionId.value);
    if (res.success) {
      message.success(
        res.data.already_terminal ? "执行已结束" : "已发送停止信号，将在当前用例结束后退出",
      );
    }
  } catch {
    message.error("停止失败");
  } finally {
    stopping.value = false;
  }
}

async function handleContinue() {
  if (!executionId.value) return;
  continuing.value = true;
  try {
    const res = await continueExecutionApi(executionId.value);
    if (res.success) {
      if (res.data.signal_delivered) {
        message.success("已推进到下一步");
      } else {
        message.info("当前不在调试暂停状态");
      }
    }
  } catch {
    message.error("发送继续信号失败");
  } finally {
    continuing.value = false;
  }
}

function goBack() {
  // 监控页可以从测试用例页（提交执行后跳转）或执行历史（点"监控"按钮）进来。
  // 旧实现走 history.back() 不可预期；显式跳到执行历史更符合"监控完接着看历史"的心智模型。
  router.push({
    name: "UIExecutionHistory",
    params: { projectId: projectId.value },
  });
}

function goDetail() {
  router.push({
    name: "UIExecutionDetail",
    params: { projectId: projectId.value, execId: executionId.value },
  });
}

// 启动顺序：先 GET 详情拿快照，再开 SSE。这样初次渲染就有数据，不至于
// 0/0/0 闪一下。两者都是 fire-and-forget，不阻塞 mount。
onMounted(async () => {
  await fetchInitial();
  sse.start();
  // 实时画面探活：失败 / 后端关掉时 liveView 保持 null，按钮自然隐藏
  try {
    const resp = await getLiveViewStatusApi();
    liveView.value = (resp as { data: LiveViewStatus }).data;
  } catch {
    liveView.value = null;
  }
});

// 实时画面是否可见（按钮 + 抽屉双关）
const liveViewAvailable = computed(() => {
  if (!liveView.value?.enabled) return false;
  // 终态执行没必要看实时——直接看视频回放更合理。剩下的状态（pending/connecting
  // /streaming/running）下保持可见，避免 SSE 还在 connecting 阶段时按钮闪烁
  return !TERMINAL.includes(meta.value.status as ExecutionStatus);
});

// noVNC iframe URL：vnc_lite.html 是 noVNC 自带的简化版（无菜单，更适合 iframe）
//   path: websockify 反代路径（不含开头的 /，因为 vnc_lite 自己会拼到 location.origin）
//   autoconnect=1 + reconnect=1: 进入 iframe 立即连接，掉线自动重连
//   resize=remote: 把 chromium 实际分辨率缩到 iframe 大小，画面不被裁
//   show_dot=1: 在没有键盘事件时画一个小光标，便于看 chromium 当前 focus
const liveViewIframeSrc = computed(() => {
  const base = liveView.value?.proxy_path || "/novnc/";
  return (
    `${base}vnc_lite.html` +
    `?path=${encodeURIComponent(`${base}websockify`)}` +
    `&autoconnect=1&reconnect=1&resize=remote&show_dot=1`
  );
});

function toggleLiveView() {
  if (showLiveView.value) {
    // 关闭：让抽屉的 v-model 切到 false 即可，iframe 在 v-if 控制下会被销毁、ws 断开
    showLiveView.value = false;
    return;
  }
  // 打开时强制重建 iframe（避免上一次未优雅关闭的 ws 连接残留把 noVNC 客户端
  // 卡在 disconnected 状态）
  liveViewIframeKey.value += 1;
  showLiveView.value = true;
}

// 抽屉宽度自适应：1920×1080 chromium 画面要"看清楚"至少需要 ~85% 缩放比，所以
// 默认给视宽的 78%（在 1080p 屏上 ≈ 1500px，noVNC resize=remote 缩放后清晰度
// 已经够用）；最低不少于 720（极窄屏直接全屏）。
const liveViewDrawerWidth = computed(() => {
  if (typeof window === "undefined") return 1280;
  const w = Math.round(window.innerWidth * 0.78);
  return Math.max(720, w);
});

onBeforeUnmount(() => {
  sse.abort();
});

// 跨执行 ID 切换（SPA 内导航） + 实时 / 回放模式切换
watch(
  () => [executionId.value, isReplayMode.value] as const,
  () => {
    sse.restart();
    fetchInitial();
  },
);
</script>

<style scoped>
.exec-monitor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.exec-monitor__overview {
  border: 1px solid var(--border-subtle);
}

/* ── 实时画面抽屉 ────────────────────────────────────────────── */
/*
 * 注意：抽屉本身的 NaiveUI 内部容器规则在文件底部的 **非 scoped** style 块里。
 * 原因：NDrawer 用 Teleport 传送到 ``<body>`` 末尾，teleport 出去的 root 拿不到
 * 父组件的 ``[data-v-xxx]`` scope，所以 ``:deep()`` 在这种场景下 **不生效**——
 * 这是上一版改了仍然没效果的根因。改用全局选择器 + 高特异性 class 限定即可。
 */
.exec-monitor__live-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.exec-monitor__live-body {
  /* 与父级 wrapper 一起组成完整的 flex 列：本容器 grow 到全部剩余高度 */
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  background: #000;
  display: flex;
  overflow: hidden;
}

.exec-monitor__live-iframe {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
}

.exec-monitor__stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.exec-monitor__stat-label {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.exec-monitor__stat-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}

.exec-monitor__stat-sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.exec-monitor__progress {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.exec-monitor__progress-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.exec-monitor__budget {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.exec-monitor__budget-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.exec-monitor__pause {
  border-left: 4px solid var(--color-info);
}

.exec-monitor__pause-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.exec-monitor__pause-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.exec-monitor__cases {
  border: 1px solid var(--border-subtle);
}

.exec-monitor__cases-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.exec-monitor__timeline-wrap {
  max-height: 480px;
  overflow-y: auto;
  padding: 4px 0;
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

.text-secondary {
  color: var(--text-secondary);
}

.mr-2 {
  margin-right: 6px;
}

.mb-3 {
  margin-bottom: 12px;
}

.mt-1 {
  margin-top: 4px;
}

.text-xs {
  font-size: 12px;
}
</style>

<!--
  ── 实时画面抽屉的 NaiveUI 内部容器覆盖 ─────────────────────────
  使用 **非 scoped** 块的两个原因（叠加）：

    1. NDrawer 通过 Teleport 把抽屉内容传送到 ``<body>`` 末尾。Vue 的 scoped
       CSS（``[data-v-xxx]``）依赖元素带 scope 属性，但 teleport 出去的 NaiveUI
       内部容器拿不到父组件 scope，所以 ``:deep([data-v-xxx]) .n-drawer-body``
       这条路径在 prod 构建里匹配不到任何节点。
    2. NaiveUI 内部容器的 padding 是用 cssr 注入的高优先级规则，必须
       ``!important`` 才能盖住。

  用 **专属 class 前缀** ``.exec-monitor__live-drawer`` 限定影响范围——这个
  class 是通过 ``<n-drawer class="exec-monitor__live-drawer">`` fallthrough 到
  抽屉根的（NaiveUI 默认 inheritAttrs:true），所以即便是全局选择器也只命中本
  抽屉，不会泄漏到项目里别的 NDrawer。
-->
<style>
.exec-monitor__live-drawer .n-drawer-body {
  padding: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #000;
}

.exec-monitor__live-drawer .n-drawer-body-content-wrapper {
  /*
   * NaiveUI 默认 .n-drawer-body-content-wrapper 是 ``position: absolute;
   * inset: 0; padding: 16px; overflow: auto``。
   * 我们要把它变成填满父容器的 flex 列、且 padding 归零，让 iframe 占满。
   * !important 是为了盖掉 NaiveUI 通过 cssr 注入的同优先级规则。
   */
  padding: 0 !important;
  display: flex !important;
  flex-direction: column !important;
  /* 保留 absolute + inset:0：这是 NaiveUI 让 wrapper 撑满 .n-drawer-body 的
     原生方案；display:flex 与 position:absolute 完全兼容 */
}

/*
 * NaiveUI 在 wrapper 里又套了一层 .n-drawer-body-content-wrapper > div（slot
 * 容器），这层默认是 block + intrinsic 高度。把它也变 flex grow。
 */
.exec-monitor__live-drawer .n-drawer-body-content-wrapper > * {
  flex: 1 1 auto;
  min-height: 0;
}

.exec-monitor__live-drawer .n-drawer-header {
  padding: 10px 16px;
}
</style>
