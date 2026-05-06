<template>
  <!-- 单条用例进度卡（Task 10.2）。
       - 标题行：序号 + 名称 + 状态徽章 + 数据可信度徽章（实时跟随 case_complete）
       - 主体：步骤列表（StepDetail）
       - 错误条：用例级 error_message（多见于 data_failure / debug timeout）
       - 折叠：默认运行中/失败的用例展开；通过的折叠以减少噪音 -->
  <n-card
    class="case-progress"
    :class="`case-progress--${caseItem.status}`"
    :bordered="false"
    size="small"
  >
    <template #header>
      <div class="case-progress__header">
        <n-tag size="small" :bordered="false" :type="statusType">
          #{{ caseItem.sort_order + 1 }}
        </n-tag>
        <!-- ``TC-0061`` 形式的人类可读编号；后端 SSE 里 ``testcase_no`` 已经
             join Testcase.case_no 取到。0 / null 兜底 ``TC-?``，与
             ``format_case_display_id`` 保持一致——这样回放页 / 实时监控页
             显示风格也跟用例列表 / 详情页统一，不会出现"用例 24835e6d"这种
             仅含 case_result_id 的难懂标识。 -->
        <span v-if="caseItem.testcase_no" class="case-progress__case-no">
          TC-{{ String(caseItem.testcase_no).padStart(4, "0") }}
        </span>
        <span class="case-progress__title">
          {{ caseTitle }}
        </span>
        <span
          v-if="caseItem.testcase_module_name"
          class="case-progress__module"
          :title="`所属模块：${caseItem.testcase_module_name}`"
        >
          · {{ caseItem.testcase_module_name }}
        </span>
        <n-tag size="tiny" :bordered="false" :type="statusType">
          {{ statusLabel }}
        </n-tag>
        <n-tag
          v-if="caseItem.data_confidence"
          :type="confidenceType"
          :bordered="false"
          size="tiny"
        >
          {{ confidenceIcon }} {{ confidenceLabel }}
        </n-tag>
        <span v-if="caseItem.duration_ms != null" class="case-progress__meta">
          <span class="i-carbon-time" />{{ formatDuration(caseItem.duration_ms) }}
        </span>
        <span v-if="caseItem.tokens_used != null && caseItem.tokens_used > 0" class="case-progress__meta">
          <span class="i-carbon-meter-alt" />{{ caseItem.tokens_used.toLocaleString() }}
        </span>
        <span v-if="caseItem.steps.length > 0" class="case-progress__meta">
          {{ completedSteps }}/{{ caseItem.steps.length }} 步
        </span>
      </div>
    </template>

    <template #header-extra>
      <n-button text size="small" @click="expanded = !expanded">
        <template #icon>
          <span :class="expanded ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
        </template>
        {{ expanded ? "折叠" : "展开" }}
      </n-button>
    </template>

    <transition name="fade">
      <div v-if="expanded" class="case-progress__body">
        <n-alert
          v-if="caseItem.error_message"
          type="error"
          :show-icon="false"
          size="small"
          class="case-progress__alert"
        >
          {{ caseItem.error_message }}
        </n-alert>

        <!-- 自造数据汇总（用例级别） -->
        <div v-if="caseItem.synthesized.length > 0" class="case-progress__synth">
          <div class="case-progress__synth-head">
            🟡 本用例 AI 自造了 {{ caseItem.synthesized.length }} 项数据
          </div>
          <div class="case-progress__synth-list">
            <span
              v-for="(s, idx) in caseItem.synthesized"
              :key="`${s.key}-${idx}`"
              class="case-progress__synth-chip"
            >
              <code>{{ s.key }}</code>
              <span v-if="s.source" class="case-progress__synth-source">
                · {{ s.source }}
              </span>
            </span>
          </div>
        </div>

        <div v-if="caseItem.steps.length === 0" class="case-progress__empty">
          <span>尚未开始执行步骤…</span>
        </div>
        <div v-else class="case-progress__steps">
          <step-detail
            v-for="step in caseItem.steps"
            :key="step.step_number"
            :step="step"
            :case-failures="caseItem.failures"
          />
        </div>
      </div>
    </transition>
  </n-card>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NAlert, NButton, NCard, NTag } from "naive-ui";
import StepDetail from "./StepDetail.vue";
import type { MonitorCase } from "@/composables/useExecutionSSE";
import type { CaseStatus, DataConfidence } from "@/services/uiAutomation";

const props = defineProps<{
  caseItem: MonitorCase;
  /** 默认是否展开；外层可控 */
  defaultExpanded?: boolean;
}>();

const expanded = ref(props.defaultExpanded ?? true);

// 默认折叠通过的用例（减少噪音），但运行中 / 失败 / 暂停状态强制展开
watch(
  () => props.caseItem.status,
  (status) => {
    if (status === "passed" || status === "skipped") {
      expanded.value = false;
    } else {
      expanded.value = true;
    }
  },
);

const completedSteps = computed(
  () => props.caseItem.steps.filter((s) => s.status !== "running" && s.status !== "pending").length,
);

/**
 * 用例标题展示降级序列：``title`` → ``case_result_id`` 前 8 位（兜底）。
 * ``TC-XXXX`` 编号已经在标题前作为独立 chip 显示，所以这里只负责标题主体；
 * 当 title 为空（用例已删除 / 老数据）时退到 ``用例 24835e6d`` 形式。
 */
const caseTitle = computed(
  () => props.caseItem.title || `用例 ${props.caseItem.case_result_id.slice(0, 8)}`,
);

const STATUS_META: Record<
  CaseStatus | "pending" | "running",
  { label: string; type: "default" | "info" | "success" | "warning" | "error" }
> = {
  pending: { label: "等待", type: "default" },
  running: { label: "执行中", type: "info" },
  passed: { label: "通过", type: "success" },
  failed: { label: "失败", type: "error" },
  error: { label: "错误", type: "error" },
  skipped: { label: "跳过", type: "warning" },
};

const statusLabel = computed(() => STATUS_META[props.caseItem.status]?.label ?? props.caseItem.status);
const statusType = computed(() => STATUS_META[props.caseItem.status]?.type ?? "default");

const CONFIDENCE_META: Record<
  Exclude<DataConfidence, null>,
  { label: string; icon: string; type: "success" | "warning" | "error" }
> = {
  reliable: { label: "数据可信", icon: "🟢", type: "success" },
  synthesized: { label: "含自造数据", icon: "🟡", type: "warning" },
  data_failure: { label: "数据失败", icon: "🟠", type: "error" },
};

const confidenceLabel = computed(() => {
  const dc = props.caseItem.data_confidence;
  return dc ? CONFIDENCE_META[dc].label : "";
});
const confidenceIcon = computed(() => {
  const dc = props.caseItem.data_confidence;
  return dc ? CONFIDENCE_META[dc].icon : "";
});
const confidenceType = computed(() => {
  const dc = props.caseItem.data_confidence;
  return dc ? CONFIDENCE_META[dc].type : "default";
});

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}
</script>

<style scoped>
.case-progress {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  margin-bottom: 12px;
  transition: border-color var(--duration-base) var(--easing-standard);
}

.case-progress--running {
  border-color: var(--color-info);
}

.case-progress--failed,
.case-progress--error {
  border-color: rgba(239, 68, 68, 0.4);
}

.case-progress--passed {
  border-color: rgba(22, 163, 74, 0.3);
}

.case-progress__header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.case-progress__title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
  margin-right: 4px;
}

.case-progress__case-no {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12px;
  font-weight: 600;
  color: var(--brand-primary);
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: var(--radius-sm);
  padding: 2px 6px;
  letter-spacing: 0.02em;
}

.case-progress__module {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 400;
}

.case-progress__meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.case-progress__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.case-progress__alert {
  margin: 0;
}

.case-progress__synth {
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
}

.case-progress__synth-head {
  font-size: 12px;
  color: #b45309;
  font-weight: 600;
  margin-bottom: 6px;
}

.case-progress__synth-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.case-progress__synth-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
  padding: 2px 8px;
  border-radius: 999px;
}

.case-progress__synth-chip code {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  background: transparent;
}

.case-progress__synth-source {
  color: rgba(180, 83, 9, 0.7);
}

.case-progress__steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.case-progress__empty {
  font-size: 13px;
  color: var(--text-tertiary);
  font-style: italic;
  padding: 12px;
  text-align: center;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--duration-base) var(--easing-standard);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
