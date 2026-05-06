/**
 * UI 自动化执行流的 SSE 状态机（Task 10.2）。
 *
 * 监控页订阅 `GET /api/ui-executions/{id}/stream`，把 ExecutionEngine 实时
 * 推送的事件折叠成"一个 execution → N 个 case → M 个 step"的层级响应式状态，
 * 让 ExecutionMonitor.vue 直接渲染、不用关心 SSE 细节。
 *
 * 与一期 chat 的区别：
 * - 一期是"模型 token 增量 + final 落 DB"——两段；
 * - UI 执行是"长事件流 + 终态 done"——多段，每段对应一个用例 / 步骤实体。
 *
 * 本 composable 只关心**事件 → 状态**的映射；网络层完全复用 `useSSE.ts`，
 * 这样将来若改用 EventSource 或 WebSocket，只需要改一处。
 *
 * 兼容三类事件来源（同一组 type/payload）：
 * 1. 实时执行（Engine + ExecutionStreamHub）
 * 2. 历史回放（replayer，payload 多一个 `replay: true` 字段）
 * 3. 重新连接（hub 已 evict，仅得到一个 done + replay_only 标记）
 *
 * 设计取舍：
 * - 步骤实时事件不带 tool_calls 详情（只给 count），详情留给 Task 10.3 详情页；
 *   replay 路径会带完整 tool_calls + reasoning + screenshot_url，本 composable
 *   也兼容这两份字段。
 * - 状态用 plain ref + 内部 mutation；不引 pinia 是因为 monitor 页一次只看一
 *   个 execution，组件销毁就完事，不需要全局 store。
 */

import { computed, ref, shallowRef } from "vue";
import { useSSE, type SSEEvent, type SSEHandle } from "./useSSE";
import type {
  CaseStatus,
  DataConfidence,
  ExecutionMode,
  ExecutionStatus,
} from "@/services/uiAutomation";

// ─── 单个步骤的渲染数据 ────────────────────────────────────────────

export type MonitorStepStatus =
  | "pending"
  | "running"
  | "passed"
  | "failed"
  | "blocked_by_security"
  | "skipped"
  | "paused";

export interface MonitorAssertion {
  passed: boolean | null;
  reason?: string | null;
  evidence?: string | null;
  method?: string | null;
}

export interface MonitorToolCall {
  /** 已剥前缀（如 ``execution_id:browser_click`` → ``browser_click``） */
  name: string;
  raw_name?: string;
  arguments?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  ok?: boolean;
  blocked?: boolean;
  duration_ms?: number;
  snapshot_chars?: number;
  error?: string | null;
}

export interface MonitorStep {
  step_number: number;
  status: MonitorStepStatus;
  /** 步骤描述（已渲染 manifest 后的文本，前 200 字符） */
  description?: string;
  duration_ms?: number;
  tokens_used?: number;
  iterations?: number;
  /** 实时模式下只有 count；replay 路径会回填 tool_calls 详情数组 */
  tool_calls_count?: number;
  tool_calls?: MonitorToolCall[];
  reasoning?: string;
  snapshot_after?: string;
  screenshot_url?: string;
  assertion?: MonitorAssertion;
  error?: string;
}

// ─── 单个用例的渲染数据 ────────────────────────────────────────────

export interface MonitorSynth {
  key: string;
  value_preview?: string;
  source?: string;
  step_id?: string | number;
}

export interface MonitorFailure {
  key: string;
  reason?: string;
  step_id?: string | number;
}

export interface MonitorCase {
  case_result_id: string;
  testcase_id?: string | null;
  /** 项目内递增的用例编号；前端用 ``TC-{case_no:04d}`` 渲染，0/null 兜底 ``TC-?`` */
  testcase_no?: number | null;
  /** 用例所属模块名称（已 join TestcaseModule.name 取出） */
  testcase_module_name?: string | null;
  title: string;
  sort_order: number;
  status: CaseStatus | "pending" | "running";
  data_confidence?: DataConfidence;
  duration_ms?: number;
  tokens_used?: number;
  error_message?: string | null;
  steps: MonitorStep[];
  synthesized: MonitorSynth[];
  failures: MonitorFailure[];
}

// ─── 整个 execution 的元数据 ───────────────────────────────────────

export interface MonitorMeta {
  execution_id: string;
  status: ExecutionStatus | "connecting" | "streaming" | "replay_only";
  mode: ExecutionMode;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number | null;
  tokens_total: number;
  error_message: string | null;
  is_replay: boolean;
  /** `execution_complete.replay_only` 时为 true：hub 已 evict，前端去拉详情 */
  is_replay_only: boolean;
}

// ─── 缺料 / 预算 / 全局事件 ────────────────────────────────────────

export interface MonitorMissingAlert {
  key: string;
  detected_in_steps: Array<{ testcase_id: string; step_number: number; where: string }>;
  will_synthesize: boolean;
}

export interface MonitorMissingBundle {
  alerts: MonitorMissingAlert[];
  strict: boolean;
}

export interface MonitorTimelineEntry {
  /** 仅用作 v-for key，递增数即可 */
  id: number;
  type: string;
  message: string;
  level: "info" | "warning" | "error" | "success";
  timestamp: number;
  payload?: Record<string, unknown>;
}

export interface MonitorPausedStep {
  case_result_id: string;
  step_number: number;
  timeout_seconds?: number;
}

// ─── Composable 入参 / 返回 ────────────────────────────────────────

export interface UseExecutionSSEOptions {
  /**
   * GET 端点。可以是一个静态字符串，也可以是 getter（每次 ``start()`` 时
   * 重新求值）——后者方便在 watcher 里通过修改外部 ref 切换"实时 ↔ 回放"
   * 端点，无需重新构造整个 composable。
   */
  url: string | (() => string);
  /** 是否以 replay 模式启动；只影响 meta.is_replay 初值 */
  isReplay?: boolean;
  mode?: ExecutionMode;
  /** 默认 50000；前端只用来画进度条，真实预算以后端为准 */
  initialTokenBudget?: number;
}

export function useExecutionSSE(opts: UseExecutionSSEOptions) {
  const { fetchSSE } = useSSE();

  const meta = ref<MonitorMeta>({
    execution_id: "",
    status: "connecting",
    mode: opts.mode ?? "normal",
    total: 0,
    passed: 0,
    failed: 0,
    skipped: 0,
    duration_ms: null,
    tokens_total: 0,
    error_message: null,
    is_replay: !!opts.isReplay,
    is_replay_only: false,
  });

  const cases = ref<MonitorCase[]>([]);
  /** 用 case_result_id → cases 数组下标 的 map 加速 update（cases 长度极大时仍 O(1)） */
  const _caseIdx = new Map<string, number>();

  const missing = shallowRef<MonitorMissingBundle | null>(null);
  const tokenBudget = ref<number>(opts.initialTokenBudget ?? 50_000);
  const tokenUsed = ref(0);
  const budgetWarning = ref<string | null>(null);
  /** 用户超 80% 时本字段被置 true；前端可弹一次提示 */
  const tokenOver80 = computed(
    () => tokenBudget.value > 0 && tokenUsed.value / tokenBudget.value >= 0.8,
  );

  const pausedStep = ref<MonitorPausedStep | null>(null);
  const timeline = ref<MonitorTimelineEntry[]>([]);
  let _timelineSeq = 0;

  /** 仅供 UI 显示的最近一条 info；timeline 是完整序列 */
  const lastInfo = ref<string | null>(null);
  const error = ref<string | null>(null);

  let _handle: SSEHandle | null = null;
  let _aborted = false;

  // ─── 内部工具 ──────────────────────────────────────────────────

  function _ensureCase(payload: Record<string, unknown>): MonitorCase | null {
    const id = String(payload.case_result_id || "");
    if (!id) return null;
    const idx = _caseIdx.get(id);
    if (idx != null) return cases.value[idx];
    const created: MonitorCase = {
      case_result_id: id,
      testcase_id: (payload.testcase_id as string) ?? null,
      testcase_no:
        typeof payload.testcase_no === "number" ? payload.testcase_no : null,
      testcase_module_name:
        (payload.testcase_module_name as string) ?? null,
      title: (payload.title as string) || "",
      sort_order:
        typeof payload.sort_order === "number" ? payload.sort_order : cases.value.length,
      status: "running",
      data_confidence: null,
      steps: [],
      synthesized: [],
      failures: [],
      error_message: null,
    };
    _caseIdx.set(id, cases.value.length);
    cases.value.push(created);
    return created;
  }

  function _ensureStep(c: MonitorCase, stepNumber: number): MonitorStep {
    const existing = c.steps.find((s) => s.step_number === stepNumber);
    if (existing) return existing;
    const created: MonitorStep = {
      step_number: stepNumber,
      status: "running",
    };
    c.steps.push(created);
    c.steps.sort((a, b) => a.step_number - b.step_number);
    return created;
  }

  /**
   * 把"已知的全量 cases"一次性灌进 composable，**同步注册到 ``_caseIdx``**——
   * 这是修复"回放页用例进度重复"故障的关键（监控页 ``fetchInitial`` 会用
   * 详情接口预填一份终态 case，但 SSE 回放又会从头发 ``case_started`` 事件，
   * 如果直接 ``cases.value.push`` 不走 ``_caseIdx``，回放事件里 ``_ensureCase``
   * 找不到记录会再创建一份 → cases 数组里两条同 ``case_result_id`` 的卡片）。
   *
   * 行为：
   * * 同 ``case_result_id`` 已存在 → 把传入字段 merge 进去（覆盖空值，保留
   *   已有非空值），不重复 push；
   * * 不存在 → push 并注册到 ``_caseIdx``，与正常 SSE 事件路径完全一致。
   *
   * 使用场景：``ExecutionMonitor.fetchInitial()`` 看到 execution 是终态时
   * 用 ``case_results`` 预填，让 SSE done 后页面也不空白。
   */
  function seedCases(rows: MonitorCase[]) {
    for (const row of rows) {
      const id = row.case_result_id;
      if (!id) continue;
      const existing = _caseIdx.get(id);
      if (existing != null) {
        // 已有 case：把 seed 里的非空字段填到现有对象上（不删除已有数据，
        // 因为可能 SSE 已经收到了一些 step 事件）
        Object.assign(cases.value[existing], {
          ...cases.value[existing],
          ...row,
          steps: row.steps.length > 0 ? row.steps : cases.value[existing].steps,
        });
        continue;
      }
      _caseIdx.set(id, cases.value.length);
      cases.value.push(row);
    }
  }

  function _pushTimeline(entry: Omit<MonitorTimelineEntry, "id" | "timestamp">) {
    timeline.value.push({
      id: ++_timelineSeq,
      timestamp: Date.now(),
      ...entry,
    });
  }

  function _stripExecutionPrefix(name: string | undefined | null): string {
    if (!name) return "";
    const idx = name.indexOf(":");
    return idx > 0 ? name.slice(idx + 1) : name;
  }

  function _normalizeToolCall(raw: unknown): MonitorToolCall {
    const obj = (raw as Record<string, unknown>) ?? {};
    const stripped = _stripExecutionPrefix(obj.name as string | undefined);
    const result = obj.result as Record<string, unknown> | undefined;
    return {
      name: stripped,
      raw_name: (obj.name as string) || stripped,
      arguments:
        typeof obj.arguments === "object"
          ? (obj.arguments as Record<string, unknown>)
          : (typeof obj.args === "object"
              ? (obj.args as Record<string, unknown>)
              : undefined),
      result,
      ok: !obj.error && !obj.blocked,
      blocked: !!obj.blocked,
      duration_ms: typeof obj.duration_ms === "number" ? obj.duration_ms : undefined,
      snapshot_chars:
        typeof obj.snapshot_chars === "number" ? obj.snapshot_chars : undefined,
      error: (obj.error as string) ?? null,
    };
  }

  // ─── 事件路由 ──────────────────────────────────────────────────

  function _dispatch(event: SSEEvent) {
    if (event.replay) meta.value.is_replay = true;

    switch (event.type) {
      case "execution_started":
        meta.value.execution_id = String(event.execution_id ?? "");
        meta.value.total = (event.total_cases as number) ?? 0;
        if (event.mode === "debug" || event.mode === "normal") {
          meta.value.mode = event.mode;
        }
        meta.value.status = "streaming";
        _pushTimeline({
          type: event.type,
          message: `开始执行（共 ${meta.value.total} 条用例）`,
          level: "info",
          payload: event,
        });
        break;

      case "bundle_ready":
        if (event.mcp_unavailable) {
          _pushTimeline({
            type: event.type,
            message: "MCP 不可用，部分浏览器工具将降级使用 Playwright SDK",
            level: "warning",
            payload: event,
          });
        } else {
          _pushTimeline({
            type: event.type,
            message: "浏览器已就绪",
            level: "info",
            payload: event,
          });
        }
        break;

      case "headless_downgraded":
        // 容器内无 DISPLAY 时 BrowserBundle 会强制降级到 headless；面板用
        // budgetWarning 这条复用通道把它显示出来（不当成 error，避免页面飘红），
        // 同时仍然写到时间线里方便追溯。
        budgetWarning.value =
          (event.message as string) ||
          "已自动忽略『有头浏览器模式』设置改用无头模式（容器无显示器）";
        _pushTimeline({
          type: event.type,
          message: "已自动降级为无头模式（容器无 DISPLAY）",
          level: "warning",
          payload: event,
        });
        break;

      case "preconditions_complete":
        _pushTimeline({
          type: event.type,
          message: "前置步骤已执行",
          level: "info",
          payload: event,
        });
        break;

      case "precondition_error":
        _pushTimeline({
          type: event.type,
          message: `前置步骤失败：${event.error ?? "unknown"}`,
          level: "error",
          payload: event,
        });
        break;

      case "missing_data_warning": {
        const alerts = (event.alerts as MonitorMissingAlert[]) ?? [];
        missing.value = {
          alerts,
          strict: !!event.strict,
        };
        _pushTimeline({
          type: event.type,
          message: `缺料告警：${alerts.length} 项`,
          level: event.strict ? "error" : "warning",
          payload: event,
        });
        break;
      }

      case "case_reset": {
        // 用例切换前的页面级清理（关多余 page + 主 page 跳 about:blank）。
        // 仅记录到 timeline，不影响 cases 数组结构。
        const closed = (event.closed_extra_pages as number) ?? 0;
        const blanked = Boolean(event.navigated_to_blank);
        const errors = Array.isArray(event.errors) ? (event.errors as string[]) : [];
        const idx = (event.next_case_index as number) ?? 0;
        const summary = errors.length
          ? `用例切换清理（#${idx + 1}）：关闭 ${closed} 个多余 page，about:blank=${blanked}，${errors.length} 项告警`
          : `用例切换清理（#${idx + 1}）：关闭 ${closed} 个多余 page，已跳 about:blank`;
        _pushTimeline({
          type: event.type,
          message: summary,
          level: errors.length ? "warning" : "info",
          payload: event,
        });
        break;
      }

      case "case_started": {
        const c = _ensureCase(event);
        if (!c) break;
        c.status = "running";
        if (event.title) c.title = event.title as string;
        if (event.testcase_id) c.testcase_id = event.testcase_id as string;
        if (typeof event.testcase_no === "number") c.testcase_no = event.testcase_no;
        if (event.testcase_module_name) {
          c.testcase_module_name = event.testcase_module_name as string;
        }
        if (typeof event.sort_order === "number") c.sort_order = event.sort_order;
        _pushTimeline({
          type: event.type,
          message: `开始用例：${c.title || c.case_result_id.slice(0, 8)}`,
          level: "info",
          payload: event,
        });
        break;
      }

      case "step_started": {
        const c = _ensureCase(event);
        if (!c) break;
        const sn = (event.step_number as number) ?? 0;
        const s = _ensureStep(c, sn);
        s.status = "running";
        if (event.action_preview) s.description = event.action_preview as string;
        // step 启动后清掉之前的 paused 标记（同一步骤被恢复）
        if (
          pausedStep.value?.case_result_id === c.case_result_id &&
          pausedStep.value?.step_number === sn
        ) {
          pausedStep.value = null;
        }
        break;
      }

      case "step_complete": {
        const c = _ensureCase(event);
        if (!c) break;
        const sn = (event.step_number as number) ?? 0;
        const s = _ensureStep(c, sn);
        const status = (event.status as MonitorStepStatus) ?? "passed";
        s.status = status;
        s.duration_ms = (event.duration_ms as number) ?? s.duration_ms;
        s.tokens_used = (event.tokens_used as number) ?? s.tokens_used;
        s.iterations = (event.iterations as number) ?? s.iterations;
        s.error = (event.error as string) ?? s.error;

        // 实时事件 tool_calls 是个数；replay 是个数组
        if (Array.isArray(event.tool_calls)) {
          s.tool_calls = (event.tool_calls as unknown[]).map(_normalizeToolCall);
          s.tool_calls_count = s.tool_calls.length;
        } else if (typeof event.tool_calls === "number") {
          s.tool_calls_count = event.tool_calls;
        } else if (typeof event.tool_calls_count === "number") {
          s.tool_calls_count = event.tool_calls_count;
        }
        if (typeof event.ai_reasoning === "string") s.reasoning = event.ai_reasoning;
        if (typeof event.snapshot_after === "string") {
          s.snapshot_after = event.snapshot_after;
        }
        if (typeof event.screenshot_url === "string") {
          s.screenshot_url = event.screenshot_url;
        }
        if (event.assertion && typeof event.assertion === "object") {
          s.assertion = event.assertion as MonitorAssertion;
        }
        _pushTimeline({
          type: event.type,
          message: `步骤 ${sn} ${stepStatusLabel(status)}（${s.duration_ms ?? "?"}ms）`,
          level:
            status === "passed"
              ? "success"
              : status === "failed" || status === "blocked_by_security"
                ? "error"
                : "info",
          payload: event,
        });
        break;
      }

      case "step_paused": {
        const c = _ensureCase(event);
        if (!c) break;
        const sn = (event.step_number as number) ?? 0;
        const s = _ensureStep(c, sn);
        s.status = "paused";
        pausedStep.value = {
          case_result_id: c.case_result_id,
          step_number: sn,
          timeout_seconds: (event.timeout_seconds as number) ?? undefined,
        };
        _pushTimeline({
          type: event.type,
          message: `步骤 ${sn} 已暂停，等待"继续"信号`,
          level: "warning",
          payload: event,
        });
        break;
      }

      case "step_resumed":
        pausedStep.value = null;
        _pushTimeline({
          type: event.type,
          message: `步骤 ${event.step_number} 已恢复`,
          level: "info",
          payload: event,
        });
        break;

      case "data_synthesized": {
        // v3.0.1 设计的"AI 自造一条数据"事件——目前后端用 case_complete 一次
        // 性回填，但若未来切到逐条推送，这里就直接挂到对应用例上
        const c = _ensureCase(event);
        if (!c || !event.key) break;
        const entry: MonitorSynth = {
          key: String(event.key),
          value_preview: event.value_preview as string | undefined,
          source: event.source as string | undefined,
          step_id: event.step_id as string | undefined,
        };
        c.synthesized.push(entry);
        _pushTimeline({
          type: event.type,
          message: `🟡 AI 自造数据：${entry.key}`,
          level: "warning",
          payload: event,
        });
        break;
      }

      case "data_failure_marked": {
        const c = _ensureCase(event);
        if (!c || !event.key) break;
        const entry: MonitorFailure = {
          key: String(event.key),
          reason: (event.reason as string) || undefined,
          step_id: event.step_id as string | undefined,
        };
        c.failures.push(entry);
        _pushTimeline({
          type: event.type,
          message: `🟠 数据失败标记：${entry.key}（${entry.reason ?? ""}）`,
          level: "error",
          payload: event,
        });
        break;
      }

      case "case_confidence": {
        const c = _ensureCase(event);
        if (!c) break;
        c.data_confidence = (event.confidence as DataConfidence) ?? null;
        break;
      }

      case "case_complete": {
        const c = _ensureCase(event);
        if (!c) break;
        c.status = (event.status as CaseStatus) ?? "passed";
        c.data_confidence = (event.data_confidence as DataConfidence) ?? null;
        c.duration_ms = (event.duration_ms as number) ?? c.duration_ms;
        c.tokens_used = (event.tokens_used as number) ?? c.tokens_used;
        c.error_message = (event.error_message as string) ?? null;

        // case_complete 也带 synthesized_data / data_failures 全集
        if (Array.isArray(event.synthesized_data) && c.synthesized.length === 0) {
          c.synthesized = (event.synthesized_data as Record<string, unknown>[]).map(
            (s) => ({
              key: String(s.key ?? ""),
              value_preview: (s.value_preview as string) ?? (s.value as string),
              source: s.source as string | undefined,
            }),
          );
        }
        if (Array.isArray(event.data_failures) && c.failures.length === 0) {
          c.failures = (event.data_failures as Record<string, unknown>[]).map((f) => ({
            key: String(f.key ?? ""),
            reason: f.reason as string | undefined,
          }));
        }

        _pushTimeline({
          type: event.type,
          message: `用例完成：${c.title || c.case_result_id.slice(0, 8)} → ${c.status}`,
          level:
            c.status === "passed"
              ? "success"
              : c.status === "skipped"
                ? "info"
                : "error",
          payload: event,
        });
        break;
      }

      case "budget_warning":
        budgetWarning.value = (event.message as string) || "Token 预算即将耗尽";
        _pushTimeline({
          type: event.type,
          message: budgetWarning.value,
          level: "warning",
          payload: event,
        });
        break;

      case "budget_exceeded":
        meta.value.status = "aborted_budget";
        meta.value.error_message = (event.message as string) || "Token 预算已超";
        _pushTimeline({
          type: event.type,
          message: meta.value.error_message,
          level: "error",
          payload: event,
        });
        break;

      case "execution_stopped":
        meta.value.status = "stopped";
        meta.value.error_message =
          (event.reason as string) || meta.value.error_message;
        _pushTimeline({
          type: event.type,
          message: `执行被停止：${event.reason ?? "user_stop"}`,
          level: "warning",
          payload: event,
        });
        break;

      case "execution_error":
        meta.value.status = "failed";
        meta.value.error_message = (event.error as string) || "执行内部错误";
        _pushTimeline({
          type: event.type,
          message: meta.value.error_message,
          level: "error",
          payload: event,
        });
        break;

      case "debug_stopped":
      case "debug_timeout":
      case "debug_timeout_pending":
        pausedStep.value = null;
        _pushTimeline({
          type: event.type,
          message:
            event.type === "debug_timeout"
              ? `调试模式 ${event.timeout_seconds ?? "?"}s 内未收到继续，已自动停止`
              : event.type === "debug_stopped"
                ? "调试模式：用户主动停止"
                : "调试暂停超时即将到来",
          level: event.type === "debug_timeout" ? "error" : "warning",
          payload: event,
        });
        break;

      case "execution_complete": {
        meta.value.status = (event.status as ExecutionStatus) ?? "completed";
        meta.value.passed = (event.passed as number) ?? meta.value.passed;
        meta.value.failed = (event.failed as number) ?? meta.value.failed;
        meta.value.skipped = (event.skipped as number) ?? meta.value.skipped;
        meta.value.duration_ms = (event.duration_ms as number) ?? meta.value.duration_ms;
        meta.value.tokens_total =
          (event.tokens_total as number) ?? meta.value.tokens_total;
        meta.value.error_message =
          (event.error_message as string) ?? meta.value.error_message;
        if (event.replay_only) meta.value.is_replay_only = true;
        tokenUsed.value = meta.value.tokens_total;
        pausedStep.value = null;
        _pushTimeline({
          type: event.type,
          message: `执行完成（${meta.value.passed}/${meta.value.failed}/${meta.value.skipped}）`,
          level: meta.value.status === "completed" ? "success" : "warning",
          payload: event,
        });
        break;
      }

      case "data_snapshot":
        _pushTimeline({
          type: event.type,
          message: "已加载执行物料快照",
          level: "info",
          payload: event,
        });
        break;

      case "info":
        if (typeof event.message === "string") {
          lastInfo.value = event.message;
          _pushTimeline({
            type: event.type,
            message: event.message,
            level: "info",
            payload: event,
          });
        }
        break;

      case "error":
        if (typeof event.message === "string") {
          error.value = event.message;
          _pushTimeline({
            type: event.type,
            message: event.message,
            level: "error",
            payload: event,
          });
        }
        break;

      default:
        // 未识别事件原样落进 timeline，方便排查协议漂移
        _pushTimeline({
          type: event.type,
          message: `未识别事件：${event.type}`,
          level: "info",
          payload: event,
        });
    }
  }

  // ─── 生命周期 ──────────────────────────────────────────────────

  function start() {
    if (_handle) return;
    _aborted = false;
    meta.value.status = "connecting";

    const resolvedUrl = typeof opts.url === "function" ? opts.url() : opts.url;
    _handle = fetchSSE(
      resolvedUrl,
      null,
      {
        onEvent(ev) {
          _dispatch(ev);
        },
        onError(msg) {
          if (_aborted) return;
          error.value = msg;
          if (
            meta.value.status !== "completed" &&
            meta.value.status !== "failed" &&
            meta.value.status !== "stopped" &&
            meta.value.status !== "aborted_budget"
          ) {
            meta.value.status = "failed";
            meta.value.error_message = msg;
          }
        },
        onDone() {
          if (
            meta.value.status === "connecting" ||
            meta.value.status === "streaming"
          ) {
            // hub evict 等 corner case：服务端没有走 execution_complete 直接 done
            // 让前端把状态固化成 completed，避免 UI 一直显示"流中"
            meta.value.status = meta.value.is_replay_only
              ? "completed"
              : meta.value.status;
          }
        },
      },
      { method: "GET" },
    );
  }

  function abort() {
    _aborted = true;
    _handle?.abort();
    _handle = null;
  }

  /** 部分场景需要重连（断线手动点击重试） */
  function restart() {
    abort();
    cases.value = [];
    _caseIdx.clear();
    timeline.value = [];
    _timelineSeq = 0;
    pausedStep.value = null;
    missing.value = null;
    budgetWarning.value = null;
    error.value = null;
    lastInfo.value = null;
    tokenUsed.value = 0;
    meta.value = {
      ...meta.value,
      status: "connecting",
      passed: 0,
      failed: 0,
      skipped: 0,
      duration_ms: null,
      tokens_total: 0,
      error_message: null,
      is_replay_only: false,
    };
    start();
  }

  return {
    meta,
    cases,
    missing,
    timeline,
    pausedStep,
    tokenBudget,
    tokenUsed,
    tokenOver80,
    budgetWarning,
    lastInfo,
    error,
    start,
    abort,
    restart,
    seedCases,
  };
}

// ─── 工具：对外友好的步骤状态文本 ──────────────────────────────────

export function stepStatusLabel(status: MonitorStepStatus | string): string {
  const map: Record<string, string> = {
    pending: "等待",
    running: "执行中",
    paused: "已暂停",
    passed: "通过",
    failed: "失败",
    blocked_by_security: "被安全策略拦截",
    skipped: "跳过",
  };
  return map[status] ?? status;
}

export function stepStatusType(
  status: MonitorStepStatus | string,
): "default" | "info" | "success" | "warning" | "error" {
  const map: Record<string, "default" | "info" | "success" | "warning" | "error"> = {
    pending: "default",
    running: "info",
    paused: "warning",
    passed: "success",
    failed: "error",
    blocked_by_security: "error",
    skipped: "warning",
  };
  return map[status] ?? "default";
}
