<template>
  <!-- 单步骤详情卡片（Task 10.2）。
       - 头部：序号 + 状态徽章 + 描述 + 耗时/tokens
       - 主体：reasoning（折叠）/ tool_calls 时间线 / 截图缩略图
       - synth 工具显示 🟡 + 自造 keys；mark_data_failure 显示 🟠 警告条
       - secret 类工具结果遮蔽（result.value 替成 "<secret used>"）

       实时 vs 回放：
       - 实时模式下后端只发 tool_calls 数量，不发详情；本组件按 count 显示
         "已调用 N 次工具，详情将在执行结束后可见"
       - 回放模式（replay endpoint）会把完整 tool_calls 数组带过来；本组件
         自动切换成时间线视图 -->
  <div class="step-detail" :class="`step-detail--${step.status}`">
    <div class="step-detail__head">
      <n-tag :type="statusType" :bordered="false" size="small">
        步骤 {{ step.step_number }}
      </n-tag>
      <span class="step-detail__status">{{ statusLabel }}</span>
      <span v-if="step.duration_ms != null" class="step-detail__meta">
        <span class="i-carbon-time" />
        {{ formatDuration(step.duration_ms) }}
      </span>
      <span v-if="step.tokens_used != null && step.tokens_used > 0" class="step-detail__meta">
        <span class="i-carbon-meter-alt" />
        {{ step.tokens_used.toLocaleString() }} tokens
      </span>
      <span v-if="step.iterations != null && step.iterations > 1" class="step-detail__meta">
        <span class="i-carbon-iteration" />
        {{ step.iterations }} 轮
      </span>
      <span v-if="hasSynthTool" class="step-detail__chip step-detail__chip--synth">
        🟡 含自造数据
      </span>
      <span v-if="hasFailureTool" class="step-detail__chip step-detail__chip--failure">
        🟠 已标记数据失败
      </span>
    </div>

    <p v-if="step.description" class="step-detail__desc">{{ step.description }}</p>

    <!-- 步骤错误 -->
    <n-alert
      v-if="step.error"
      type="error"
      class="step-detail__alert"
      :show-icon="false"
      size="small"
    >
      {{ step.error }}
    </n-alert>

    <!-- AssertionJudge 结论（已 fail 时强调原因） -->
    <n-alert
      v-if="assertionFailed"
      type="warning"
      :show-icon="false"
      class="step-detail__alert"
      size="small"
    >
      <strong>断言未通过：</strong>{{ step.assertion?.reason || "无判定原因" }}
      <template v-if="step.assertion?.evidence">
        <div class="step-detail__assertion-evidence">证据：{{ step.assertion.evidence }}</div>
      </template>
    </n-alert>

    <!-- mark_data_failure 警告条（独立于 assertion） -->
    <n-alert
      v-if="markedFailure"
      type="error"
      :show-icon="false"
      size="small"
      class="step-detail__alert"
    >
      <strong>🟠 AI 主动标记数据失败：</strong>{{ markedFailure.reason }}
    </n-alert>

    <!-- reasoning -->
    <n-collapse v-if="step.reasoning" arrow-placement="right" class="step-detail__rc">
      <n-collapse-item title="AI 推理过程">
        <pre class="step-detail__reasoning">{{ step.reasoning }}</pre>
      </n-collapse-item>
    </n-collapse>

    <!-- tool_calls 时间线（实时只有 count；回放有详情） -->
    <div v-if="hasToolDetails" class="step-detail__tools">
      <div class="step-detail__tools-head">
        <span class="i-carbon-tool-kit" />
        Tool Calls 时间线（{{ step.tool_calls?.length }} 次）
      </div>
      <n-timeline size="medium" class="step-detail__timeline">
        <n-timeline-item
          v-for="(tc, idx) in step.tool_calls"
          :key="idx"
          :type="toolItemType(tc)"
          :title="tc.name || '(unknown tool)'"
        >
          <template #header>
            <div class="step-detail__tool-head">
              <code class="step-detail__tool-name">{{ tc.name }}</code>
              <span v-if="isSynthTool(tc)" class="step-detail__chip step-detail__chip--synth">
                🟡 synth
              </span>
              <span v-if="isFailureTool(tc)" class="step-detail__chip step-detail__chip--failure">
                🟠 mark_failure
              </span>
              <span v-if="tc.blocked" class="step-detail__chip step-detail__chip--blocked">
                被安全策略拦截
              </span>
              <span v-if="tc.duration_ms" class="step-detail__tool-meta">
                {{ formatDuration(tc.duration_ms) }}
              </span>
            </div>
          </template>
          <div v-if="tc.arguments && Object.keys(tc.arguments).length > 0">
            <div class="step-detail__tool-section-title">参数</div>
            <pre class="step-detail__tool-pre">{{ formatJSON(tc.arguments) }}</pre>
          </div>
          <div v-if="redactedResult(tc) !== null">
            <div class="step-detail__tool-section-title">返回（已脱敏 secret）</div>
            <pre class="step-detail__tool-pre">{{ formatJSON(redactedResult(tc)) }}</pre>
          </div>
          <div v-if="tc.error" class="step-detail__tool-error">
            错误：{{ tc.error }}
          </div>
        </n-timeline-item>
      </n-timeline>
    </div>

    <div
      v-else-if="(step.tool_calls_count ?? 0) > 0"
      class="step-detail__tools-summary"
    >
      <span class="i-carbon-tool-kit" />
      已调用 {{ step.tool_calls_count }} 次工具
      <span class="step-detail__hint">详细时间线在执行结束后可见</span>
    </div>

    <!-- 截图 -->
    <live-screenshot
      v-if="step.screenshot_url"
      :url="step.screenshot_url"
      caption="步骤截图"
      class="step-detail__shot"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NAlert, NCollapse, NCollapseItem, NTag, NTimeline, NTimelineItem } from "naive-ui";
import LiveScreenshot from "./LiveScreenshot.vue";
import {
  stepStatusLabel,
  stepStatusType,
  type MonitorFailure,
  type MonitorStep,
  type MonitorToolCall,
} from "@/composables/useExecutionSSE";

const props = defineProps<{
  step: MonitorStep;
  /** 用例上的 failure 列表（来自 case.failures），用于在该步骤里渲染🟠卡片 */
  caseFailures?: MonitorFailure[];
}>();

const statusType = computed(() => stepStatusType(props.step.status));
const statusLabel = computed(() => stepStatusLabel(props.step.status));

const hasToolDetails = computed(
  () => Array.isArray(props.step.tool_calls) && props.step.tool_calls.length > 0,
);

function isSynthTool(tc: MonitorToolCall): boolean {
  return tc.name === "platform_synthesize_data";
}

function isFailureTool(tc: MonitorToolCall): boolean {
  return tc.name === "platform_mark_data_failure";
}

const hasSynthTool = computed(
  () => hasToolDetails.value && props.step.tool_calls!.some(isSynthTool),
);

const hasFailureTool = computed(
  () => hasToolDetails.value && props.step.tool_calls!.some(isFailureTool),
);

const assertionFailed = computed(() => {
  if (!props.step.assertion) return false;
  // explicit false (not null) means judged as failed
  return props.step.assertion.passed === false;
});

/**
 * 在该步骤里检测：本步骤的 tool_call 中是否触发了"标记数据失败"——若有，
 * 配合用例级别的 failures 列表显示 🟠 卡。匹配策略：用 step_id 字段；找不到
 * 就拿用例最新一条 failure 兜底（保守选择，避免空显示）。
 */
const markedFailure = computed<MonitorFailure | null>(() => {
  if (!hasFailureTool.value || !props.caseFailures || props.caseFailures.length === 0) {
    return null;
  }
  // step_id 字段在 SSE 事件里以"case_result_id:step_number"这种字符串出现的概
  // 率大；后端 data_failure_marked 事件未来若加 step_id 字段，本组件直接生效
  const matched = props.caseFailures.find(
    (f) =>
      typeof f.step_id === "string" && f.step_id.endsWith(`:${props.step.step_number}`),
  );
  return matched ?? props.caseFailures[props.caseFailures.length - 1];
});

function toolItemType(tc: MonitorToolCall): "default" | "info" | "success" | "warning" | "error" {
  if (tc.error || tc.blocked) return "error";
  if (isFailureTool(tc)) return "error";
  if (isSynthTool(tc)) return "warning";
  if (tc.name?.startsWith("platform_get_secret")) return "info";
  return "success";
}

const SECRET_TOOL_NAMES = new Set([
  "platform_get_secret",
]);

function redactedResult(tc: MonitorToolCall): unknown {
  if (tc.result == null) return null;
  // 后端 persistence 已经在落库前对 secret 做了脱敏；前端再加一道保险
  if (
    SECRET_TOOL_NAMES.has(tc.name) ||
    tc.result?._test_data_secret_used
  ) {
    const redacted = { ...tc.result };
    if ("value" in redacted) {
      redacted.value = "<secret used>";
    }
    return redacted;
  }
  return tc.result;
}

function formatDuration(ms: number | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatJSON(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
</script>

<style scoped>
.step-detail {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color var(--duration-fast) var(--easing-standard);
}

.step-detail--running {
  border-color: var(--color-info);
  box-shadow: 0 0 0 2px rgba(14, 165, 233, 0.1);
}

.step-detail--paused {
  border-color: var(--color-warning);
  background: rgba(245, 158, 11, 0.04);
}

.step-detail--failed,
.step-detail--blocked_by_security {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.03);
}

.step-detail__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.step-detail__status {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-secondary);
}

.step-detail__meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.step-detail__chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  height: 18px;
  line-height: 18px;
}

.step-detail__chip--synth {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.step-detail__chip--failure {
  background: rgba(239, 68, 68, 0.16);
  color: #b91c1c;
}

.step-detail__chip--blocked {
  background: rgba(15, 23, 42, 0.08);
  color: var(--text-secondary);
}

.step-detail__desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}

.step-detail__alert {
  margin: 0;
}

.step-detail__assertion-evidence {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}

.step-detail__rc {
  margin-top: 4px;
}

.step-detail__reasoning {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-page-soft);
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  max-height: 240px;
  overflow-y: auto;
}

.step-detail__tools {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-detail__tools-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 600;
}

.step-detail__timeline {
  margin-top: 4px;
}

.step-detail__tool-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.step-detail__tool-name {
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

.step-detail__tool-meta {
  color: var(--text-tertiary);
  font-size: 11px;
}

.step-detail__tool-section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.step-detail__tool-pre {
  margin: 4px 0;
  padding: 8px 10px;
  background: var(--bg-page-soft);
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--text-secondary);
}

.step-detail__tool-error {
  font-size: 12px;
  color: var(--color-error);
  margin-top: 4px;
}

.step-detail__tools-summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  padding: 6px 10px;
  border-radius: var(--radius-sm);
}

.step-detail__hint {
  font-size: 11px;
  margin-left: 4px;
  font-style: italic;
}

.step-detail__shot {
  margin-top: 2px;
}
</style>
