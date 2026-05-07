<template>
  <!-- 测试报告卡片 — 针对单次 UI 自动化执行的"汇总分析"。
       与"执行详情"其它模块的差异：
       - 这里**不**展示原始事件流 / 步骤 tool_call 等过程信息，而是给一份
         "结论 + 用例清单 + 风险提示 + 改进建议"的 narrative，让团队一眼看
         懂本次跑得"好不好"、问题在哪、下一步该干嘛。
       - 报告的**主体是测试用例**（id / 标题 / 所属模块），而不是 UI 执行任务
         本身的 ID/描述——"任务"是触发器，"用例"才是被验证的业务单元。
       - 提供"复制 Markdown / 下载 Markdown"两个入口。 -->
  <n-card size="small" class="test-report" :bordered="false">
    <template #header>
      <div class="test-report__head">
        <span class="i-carbon-report" />
        <span class="test-report__title">测试报告</span>
        <n-tag :type="overallTagType" :bordered="false" size="small">
          {{ overallTagLabel }}
        </n-tag>
      </div>
    </template>
    <template #header-extra>
      <n-space :size="6">
        <n-button quaternary size="tiny" @click="copyMarkdown">
          <template #icon><span class="i-carbon-copy" /></template>
          复制
        </n-button>
        <n-button quaternary size="tiny" @click="downloadMarkdown">
          <template #icon><span class="i-carbon-download" /></template>
          下载 Markdown
        </n-button>
      </n-space>
    </template>

    <!-- 总体结论 -->
    <div class="test-report__verdict">
      <span class="test-report__verdict-icon">{{ verdict.emoji }}</span>
      <p class="test-report__verdict-text">{{ verdict.text }}</p>
    </div>

    <!-- 用例数量统计：通过 / 失败 / 跳过 / 总数 -->
    <div class="test-report__section">
      <div class="test-report__section-title">用例数量统计</div>
      <div class="test-report__chips">
        <span class="test-report__chip test-report__chip--total">
          📋 用例总数 <strong>{{ detail.total_cases }}</strong>
        </span>
        <span class="test-report__chip test-report__chip--ok">
          ✅ 通过 <strong>{{ detail.passed_cases }}</strong>
        </span>
        <span class="test-report__chip test-report__chip--err">
          ❌ 失败 <strong>{{ detail.failed_cases }}</strong>
        </span>
        <span class="test-report__chip test-report__chip--mute">
          ⏭️ 跳过 <strong>{{ detail.skipped_cases }}</strong>
        </span>
      </div>
    </div>

    <!-- 通过用例清单（默认折叠） -->
    <div v-if="passedCases.length > 0" class="test-report__section">
      <details class="test-report__details">
        <summary class="test-report__summary">
          <span class="test-report__summary-icon">✅</span>
          通过用例清单（{{ passedCases.length }} 项）
        </summary>
        <ul class="test-report__cases">
          <li v-for="c in passedCases" :key="c.id" class="test-report__case">
            <div class="test-report__case-head">
              <n-tag size="tiny" type="info" :bordered="false">
                {{ formatCaseCode(c) }}
              </n-tag>
              <span class="test-report__case-title">{{ caseTitleOf(c) }}</span>
              <span v-if="c.testcase_module_name" class="test-report__case-module">
                {{ c.testcase_module_name }}
              </span>
            </div>
          </li>
        </ul>
      </details>
    </div>

    <!-- 失败用例清单 -->
    <div v-if="failedCases.length > 0" class="test-report__section">
      <div class="test-report__section-title">
        失败用例清单（{{ failedCases.length }} 项）
      </div>
      <ul class="test-report__cases">
        <li v-for="c in failedCases" :key="c.id" class="test-report__case">
          <div class="test-report__case-head">
            <n-tag size="tiny" type="error" :bordered="false">
              {{ formatCaseCode(c) }}
            </n-tag>
            <span class="test-report__case-title">{{ caseTitleOf(c) }}</span>
            <span v-if="c.testcase_module_name" class="test-report__case-module">
              {{ c.testcase_module_name }}
            </span>
            <n-tag size="tiny" type="error" :bordered="false" class="ml-auto">
              {{ statusLabel(c.status) }}
            </n-tag>
          </div>
          <div v-if="c.error_message" class="test-report__case-error">
            {{ truncate(c.error_message, 280) }}
          </div>
        </li>
      </ul>
    </div>

    <!-- 跳过用例清单 -->
    <div v-if="skippedCases.length > 0" class="test-report__section">
      <details class="test-report__details">
        <summary class="test-report__summary">
          <span class="test-report__summary-icon">⏭️</span>
          跳过用例清单（{{ skippedCases.length }} 项）
        </summary>
        <ul class="test-report__cases">
          <li v-for="c in skippedCases" :key="c.id" class="test-report__case">
            <div class="test-report__case-head">
              <n-tag size="tiny" :bordered="false">
                {{ formatCaseCode(c) }}
              </n-tag>
              <span class="test-report__case-title">{{ caseTitleOf(c) }}</span>
              <span v-if="c.testcase_module_name" class="test-report__case-module">
                {{ c.testcase_module_name }}
              </span>
            </div>
          </li>
        </ul>
      </details>
    </div>

    <!-- 改进建议 -->
    <div v-if="suggestions.length > 0" class="test-report__section">
      <div class="test-report__section-title">改进建议</div>
      <ul class="test-report__suggestions">
        <li v-for="(s, i) in suggestions" :key="i">{{ s }}</li>
      </ul>
    </div>
  </n-card>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NCard, NTag, NButton, NSpace, useMessage } from "naive-ui";
import type {
  ExecutionDetailResponse,
  ExecutionCaseResponse,
} from "@/services/uiAutomation";

const props = defineProps<{
  detail: ExecutionDetailResponse;
}>();

const message = useMessage();

// ─── 派生：用例分组（按 status 拆通过 / 失败 / 跳过）─────────────

const passedCases = computed<ExecutionCaseResponse[]>(() =>
  props.detail.case_results
    .filter((c) => c.status === "passed")
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order),
);

const failedCases = computed<ExecutionCaseResponse[]>(() =>
  props.detail.case_results
    .filter((c) => c.status === "failed" || c.status === "error")
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order),
);

const skippedCases = computed<ExecutionCaseResponse[]>(() =>
  props.detail.case_results
    .filter((c) => c.status === "skipped")
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order),
);

// ─── 派生：可信度计数（仅用于结论判定，不再以卡片形式展示）─────

const confidence = computed(() => {
  let reliable = 0;
  let synthesized = 0;
  let data_failure = 0;
  for (const c of props.detail.case_results) {
    if (c.data_confidence === "reliable") reliable += 1;
    else if (c.data_confidence === "synthesized") synthesized += 1;
    else if (c.data_confidence === "data_failure") data_failure += 1;
  }
  return { reliable, synthesized, data_failure };
});

// ─── 总体结论（基于"用例通过情况 + 数据可信度"自动生成中文 verdict）

// 是否还在跑：``running``、``pending`` 都视为未完成；这两种状态下报告
// 还在累计中，不能给"质量良好"等终态结论，否则用户会误以为已经测完。
const isInProgress = computed(
  () => props.detail.status === "running" || props.detail.status === "pending",
);

const verdict = computed<{ emoji: string; text: string }>(() => {
  const d = props.detail;
  if (isInProgress.value) {
    const finished = d.passed_cases + d.failed_cases + d.skipped_cases;
    return {
      emoji: "⏳",
      text:
        d.total_cases > 0
          ? `执行中… 已完成 ${finished} / ${d.total_cases} 条用例（通过 ${d.passed_cases} · 失败 ${d.failed_cases} · 跳过 ${d.skipped_cases}），全部跑完后将自动给出结论。`
          : "执行已启动，等待第一条用例事件…",
    };
  }
  if (d.status === "aborted_budget") {
    return {
      emoji: "🛑",
      text: "本次执行因 Token 预算耗尽被强制终止，请考虑放宽预算或精简用例步骤后重跑。",
    };
  }
  if (d.status === "stopped") {
    return {
      emoji: "⏹️",
      text: `本次执行被用户主动停止；已完成 ${
        d.passed_cases + d.failed_cases + d.skipped_cases
      } / ${d.total_cases} 用例。`,
    };
  }
  // 整次执行被标 ``failed`` 的最常见原因：前置步骤未通过 / 浏览器 bundle 启
  // 动失败 / 严格物料模式拒绝。这些情况下 ``total_cases`` 仍 = ``len(testcase_ids)``
  // 但 case_results 是空的，``passed/failed/skipped`` 全是 0 —— 不能再走"全
  // 部通过"分支误报"质量良好"。结论文案优先用 ``error_message``。
  if (d.status === "failed") {
    const finished = d.passed_cases + d.failed_cases + d.skipped_cases;
    if (finished === 0) {
      const reason = (d.error_message || "").trim();
      return {
        emoji: "❌",
        text: reason
          ? `本次执行失败，未跑任何用例。原因：${truncate(reason, 240)}`
          : "本次执行整体失败（前置步骤 / 浏览器 / 物料预检异常），未跑任何用例，详见错误信息。",
      };
    }
    return {
      emoji: "❌",
      text: `本次执行整体失败：${d.passed_cases} / ${d.total_cases} 通过，${d.failed_cases} 条失败、${d.skipped_cases} 条未执行；建议先排查执行级错误。`,
    };
  }
  if (d.total_cases === 0) {
    return { emoji: "📋", text: "本次执行没有任何用例参与，无可分析数据。" };
  }
  // 兜底防御：``status`` 不是 failed/stopped/aborted_budget 等已知失败态，
  // 也不是 in_progress，但实际 case_results 为空（passed+failed+skipped=0
  // 而 total_cases>0）。这一定是异常状态，不能给"全部通过"的乐观结论。
  const finished = d.passed_cases + d.failed_cases + d.skipped_cases;
  if (finished === 0) {
    return {
      emoji: "⚠️",
      text: `本次执行未产出用例结果（${d.total_cases} 条用例均未执行）；可能是前置步骤异常或调度提前中断，请查看执行日志。`,
    };
  }
  // 同样兜底：已跑用例数小于总数，但执行整体不在终态错误中——也不能简单
  // 喊"全部通过"。
  if (d.failed_cases === 0 && d.skipped_cases === 0 && finished < d.total_cases) {
    return {
      emoji: "⚠️",
      text: `${d.passed_cases} / ${d.total_cases} 已通过，但仍有 ${d.total_cases - finished} 条用例未产出结果，请确认执行是否被中断。`,
    };
  }
  if (d.failed_cases === 0 && d.skipped_cases === 0) {
    if (confidence.value.synthesized === 0) {
      return {
        emoji: "🎉",
        text: `质量良好：${d.total_cases} 条用例全部通过，且全部使用真实物料数据，结果可信度高。`,
      };
    }
    return {
      emoji: "✅",
      text: `${d.total_cases} 条用例全部通过；其中 ${confidence.value.synthesized} 条触发了 AI 自造数据，建议人工复核。`,
    };
  }
  if (d.failed_cases === 0) {
    return {
      emoji: "✅",
      text: `${d.passed_cases} 条用例通过，${d.skipped_cases} 条跳过；无失败项。`,
    };
  }
  // 少量失败（≤10%）。禁止使用 Math.max(1, floor(n*0.1))：n=1 时会把「全败」误
  // 判进「整体表现良好」（floor(0.1)=0 与 max(1,*) 组合曾导致该 bug）。
  if (d.failed_cases <= Math.floor(d.total_cases * 0.1)) {
    return {
      emoji: "⚠️",
      text: `整体表现良好：${d.passed_cases} / ${d.total_cases} 通过，${d.failed_cases} 条失败需关注。`,
    };
  }
  if (d.failed_cases <= Math.floor(d.total_cases * 0.3)) {
    return {
      emoji: "⚠️",
      text: `质量一般：失败 ${d.failed_cases} / ${d.total_cases}，建议先修复主要失败用例再进入下一轮回归。`,
    };
  }
  return {
    emoji: "🔴",
    text: `质量风险较高：失败 ${d.failed_cases} / ${d.total_cases}，建议先排查共性问题（环境 / 物料 / 用例稳定性）再继续。`,
  };
});

const overallTagType = computed<
  "success" | "warning" | "error" | "info" | "default"
>(() => {
  const s = props.detail.status;
  const d = props.detail;
  if (s === "completed") {
    if (d.failed_cases === 0) return "success";
    if (d.failed_cases <= Math.floor(d.total_cases * 0.3)) return "warning";
    return "error";
  }
  if (s === "running") return "info";
  if (s === "stopped" || s === "aborted_budget") return "warning";
  return "default";
});

const overallTagLabel = computed(() => {
  const map: Record<string, string> = {
    pending: "排队中",
    running: "执行中",
    completed: "已完成",
    failed: "执行失败",
    stopped: "已停止",
    aborted_budget: "预算耗尽",
  };
  return map[props.detail.status] || props.detail.status;
});

// ─── 改进建议 ─────────────────────────────────────────────────────

const suggestions = computed<string[]>(() => {
  const tips: string[] = [];
  const d = props.detail;
  // 执行中不出"改进建议"——还没跑完就给整改方向，会让人误以为这就是终
  // 态结论。等到 status 变成 completed/failed/stopped 再统一给。
  if (isInProgress.value) return tips;
  // 整次执行就失败了（典型：前置步骤未通过 / bundle 启动失败）—— 此时不
  // 应该再给"用例级"的整改建议，那些建议会迷惑用户。直接引导"先解决执
  // 行级错误"即可。
  const finishedCases = d.passed_cases + d.failed_cases + d.skipped_cases;
  if (d.status === "failed" && finishedCases === 0) {
    tips.push(
      "本次执行未跑任何用例：请先点击「执行错误」处的提示排查环境/前置步骤；解决后再次发起执行即可。",
    );
    return tips;
  }
  if (d.total_cases > 0 && d.failed_cases / d.total_cases > 0.3) {
    tips.push("失败比例偏高，建议先聚焦失败用例的共性问题（如登录态、关键页面变更）。");
  }
  if (confidence.value.data_failure > 0) {
    tips.push(
      "存在数据失败用例：可在「测试物料」中补全对应 key，或检查环境是否绑定了正确的物料集。",
    );
  }
  if (
    confidence.value.synthesized > 0 &&
    confidence.value.synthesized > confidence.value.reliable
  ) {
    tips.push(
      "AI 自造数据已超过真实数据：长期建议把高频 key 沉淀到物料集，提升执行稳定性。",
    );
  }
  if (d.failed_cases > 0 && d.skipped_cases === 0) {
    tips.push(
      "可在执行详情页点击「重跑失败用例」快速验证修复效果，无需重复跑全量。",
    );
  }
  return tips;
});

// ─── 工具函数 ─────────────────────────────────────────────────────

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    passed: "通过",
    failed: "失败",
    error: "异常",
    skipped: "跳过",
    pending: "排队",
    running: "执行中",
  };
  return map[s] || s;
}

function caseTitleOf(c: ExecutionCaseResponse): string {
  if (c.testcase_title) return c.testcase_title;
  if (c.testcase_id) {
    return `(${formatCaseCode(c)}，可能已被删除)`;
  }
  return "(未关联用例)";
}

/** 业务编号渲染：``TC-0117``（与「测试用例管理」页保持一致）。
 *  用例已被删除导致 case_no 缺失时，回退到 UUID 前 8 位作为粗略追溯。 */
function formatCaseCode(c: ExecutionCaseResponse): string {
  if (c.testcase_no != null && c.testcase_no > 0) {
    return `TC-${String(c.testcase_no).padStart(4, "0")}`;
  }
  if (c.testcase_id) return `TC-${c.testcase_id.slice(0, 8)}`;
  return "—";
}

function truncate(s: string | null | undefined, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

// ─── Markdown 导出 ────────────────────────────────────────────────

function buildMarkdown(): string {
  const d = props.detail;
  const lines: string[] = [];

  lines.push(`# 测试报告 — ${overallTagLabel.value}`);
  lines.push("");
  lines.push(`${verdict.value.emoji} ${verdict.value.text}`);
  lines.push("");

  lines.push("## 用例数量统计");
  lines.push("");
  lines.push(`- 用例总数：${d.total_cases}`);
  lines.push(`- ✅ 通过：${d.passed_cases}`);
  lines.push(`- ❌ 失败：${d.failed_cases}`);
  lines.push(`- ⏭️ 跳过：${d.skipped_cases}`);
  lines.push("");

  function writeCases(title: string, cases: ExecutionCaseResponse[]): void {
    if (cases.length === 0) return;
    lines.push(`## ${title}（${cases.length} 项）`);
    lines.push("");
    for (const c of cases) {
      lines.push(`### ${formatCaseCode(c)} ${caseTitleOf(c)}`);
      if (c.testcase_module_name) {
        lines.push(`- 所属模块：${c.testcase_module_name}`);
      }
      lines.push(`- 状态：${statusLabel(c.status)}`);
      if (c.error_message) {
        lines.push(`- 错误信息：${truncate(c.error_message, 280)}`);
      }
      lines.push("");
    }
  }

  writeCases("失败用例清单", failedCases.value);
  writeCases("通过用例清单", passedCases.value);
  writeCases("跳过用例清单", skippedCases.value);

  if (suggestions.value.length > 0) {
    lines.push("## 改进建议");
    lines.push("");
    for (const s of suggestions.value) lines.push(`- ${s}`);
    lines.push("");
  }
  return lines.join("\n");
}

async function copyMarkdown() {
  const md = buildMarkdown();
  try {
    await navigator.clipboard.writeText(md);
    message.success("报告已复制到剪贴板");
  } catch {
    message.error("复制失败，请检查浏览器剪贴板权限");
  }
}

function downloadMarkdown() {
  const md = buildMarkdown();
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `ui-test-report-${props.detail.id.slice(0, 8)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
</script>

<style scoped>
.test-report :deep(.n-card-header) {
  padding-bottom: 8px;
}

.test-report__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.test-report__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.test-report__verdict {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-page-soft, var(--bg-active));
  border: 1px solid var(--border-subtle);
}

.test-report__verdict-icon {
  font-size: 22px;
  line-height: 1.2;
}

.test-report__verdict-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
}

.test-report__section {
  margin-top: 14px;
}

.test-report__section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.test-report__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.test-report__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid transparent;
}

.test-report__chip strong {
  font-weight: 700;
}

.test-report__chip--total {
  background: rgba(64, 128, 255, 0.1);
  color: #1d4ed8;
  border-color: rgba(64, 128, 255, 0.25);
}

.test-report__chip--ok {
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
  border-color: rgba(22, 163, 74, 0.25);
}

.test-report__chip--err {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.3);
}

.test-report__chip--mute {
  background: var(--bg-page-soft);
  color: var(--text-tertiary);
  border-color: var(--border-subtle);
}

.test-report__cases {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.test-report__case {
  padding: 10px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.test-report__case-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.test-report__case-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}

.test-report__case-module {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--bg-page-soft);
  color: var(--text-tertiary);
  border: 1px solid var(--border-subtle);
}

.test-report__case-meta {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono, ui-monospace, monospace);
  word-break: break-all;
}

.test-report__case-error {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: #b91c1c;
  background: rgba(239, 68, 68, 0.06);
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  border-left: 3px solid rgba(239, 68, 68, 0.5);
}

.test-report__details > .test-report__summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  user-select: none;
  margin-bottom: 8px;
}

.test-report__summary-icon {
  font-size: 14px;
}

.test-report__suggestions {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}
</style>
