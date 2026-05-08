import { request } from "./request";
import type { ApiResponse } from "./auth";

// ─── 类型定义（与后端 app/modules/ui_automation/schemas.py 严格对齐）──────

export type PreconditionType =
  | "state_inject"
  | "ai_login"
  | "scripted_steps"
  | "cookie_inject"
  | "http_login";

export interface PreconditionTemplate {
  id: string;
  environment_id: string;
  name: string;
  type: PreconditionType;
  description: string | null;
  config: Record<string, unknown>;
  has_credentials: boolean;
  order_index: number;
  enabled: boolean;
  state_saved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestEnvironment {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  base_url: string;
  allowed_hosts: string[];
  token_budget: number;
  enable_browser_evaluate: boolean;
  session_name: string | null;
  state_saved_at: string | null;
  default_data_set_ids: string[];
  headless: boolean;
  viewport_width: number;
  viewport_height: number;
  created_at: string;
  updated_at: string;
}

export interface TestEnvironmentDetail extends TestEnvironment {
  preconditions: PreconditionTemplate[];
}

export interface PaginatedEnvironments {
  items: TestEnvironment[];
  total: number;
  page: number;
  page_size: number;
}

export interface EnvironmentCreateParams {
  name: string;
  description?: string;
  base_url: string;
  allowed_hosts?: string[];
  token_budget?: number;
  enable_browser_evaluate?: boolean;
  session_name?: string | null;
  default_data_set_ids?: string[];
  headless?: boolean;
  viewport_width?: number;
  viewport_height?: number;
}

export interface EnvironmentUpdateParams {
  name?: string;
  description?: string | null;
  base_url?: string;
  allowed_hosts?: string[];
  token_budget?: number;
  enable_browser_evaluate?: boolean;
  session_name?: string | null;
  default_data_set_ids?: string[];
  headless?: boolean;
  viewport_width?: number;
  viewport_height?: number;
}

export interface PreconditionCreateParams {
  name: string;
  type: PreconditionType;
  description?: string | null;
  config?: Record<string, unknown>;
  /**
   * 敏感凭据。请求时是明文 dict，后端 Fernet 加密后存 credentials_encrypted
   * 字段；查询时 API 不会返回明文，只通过 has_credentials 暴露存在性。
   */
  credentials?: Record<string, unknown> | null;
  order_index?: number;
  enabled?: boolean;
}

export interface PreconditionUpdateParams {
  name?: string;
  type?: PreconditionType;
  description?: string | null;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown> | null;
  /**
   * 显式清空已存凭据。与 credentials=null 区别：
   *   - credentials=null → 不改
   *   - clear_credentials=true → 清空（优先级高于 credentials）
   */
  clear_credentials?: boolean;
  order_index?: number;
  enabled?: boolean;
}

export interface ClearStateResult {
  environment_id: string;
  state_file_existed: boolean;
  state_file_removed: boolean;
}

export interface TestPreconditionResult {
  template_id: string;
  template_name: string;
  type: PreconditionType;
  success: boolean;
  elapsed_ms: number;
  error: string | null;
  error_kind: string | null;
  screenshot_base64: string | null;
  state_was_loaded: boolean;
  state_was_stale: boolean;
  state_was_saved: boolean;
  state_saved_path: string | null;
  fell_back_to: string | null;
  logs: string[];
}

// ─── 环境 CRUD ────────────────────────────────────────────────────────

export function listEnvironmentsApi(
  projectId: string,
  params: { page?: number; page_size?: number } = {},
) {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  const qs = q.toString();
  return request<ApiResponse<PaginatedEnvironments>>(
    `/projects/${projectId}/ui-environments${qs ? `?${qs}` : ""}`,
  );
}

export function createEnvironmentApi(
  projectId: string,
  data: EnvironmentCreateParams,
) {
  return request<ApiResponse<TestEnvironmentDetail>>(
    `/projects/${projectId}/ui-environments`,
    { method: "POST", body: data },
  );
}

export function getEnvironmentApi(envId: string) {
  return request<ApiResponse<TestEnvironmentDetail>>(
    `/ui-environments/${envId}`,
  );
}

export function updateEnvironmentApi(
  envId: string,
  data: EnvironmentUpdateParams,
) {
  return request<ApiResponse<TestEnvironmentDetail>>(
    `/ui-environments/${envId}`,
    { method: "PATCH", body: data },
  );
}

export function deleteEnvironmentApi(envId: string) {
  return request<ApiResponse<null>>(`/ui-environments/${envId}`, {
    method: "DELETE",
  });
}

export function clearEnvironmentStateApi(envId: string) {
  return request<ApiResponse<ClearStateResult>>(
    `/ui-environments/${envId}/clear-state`,
    { method: "POST" },
  );
}

// ─── 有头浏览器实时画面（noVNC）─────────────────────────────────────

export interface LiveViewStatus {
  /** 后端检测 + websockify 端口探活后的最终结果。仅 true 时前端展示按钮。 */
  enabled: boolean;
  /** 前端拼 iframe URL 用，统一是 ``/novnc/``，由 nginx 反代到 backend:6080。 */
  proxy_path: string;
  port: number;
  /** enabled=false 时给出原因（端口未监听 / 关掉 / etc），用于运维诊断。 */
  hint: string | null;
}

export function getLiveViewStatusApi() {
  return request<ApiResponse<LiveViewStatus>>(`/ui-automation/live-view/status`);
}

// ─── 前置步骤 CRUD ────────────────────────────────────────────────────

export function listPreconditionsApi(envId: string) {
  return request<ApiResponse<{ items: PreconditionTemplate[] }>>(
    `/ui-environments/${envId}/preconditions`,
  );
}

export function createPreconditionApi(
  envId: string,
  data: PreconditionCreateParams,
) {
  return request<ApiResponse<PreconditionTemplate>>(
    `/ui-environments/${envId}/preconditions`,
    { method: "POST", body: data },
  );
}

export function updatePreconditionApi(
  preconditionId: string,
  data: PreconditionUpdateParams,
) {
  return request<ApiResponse<PreconditionTemplate>>(
    `/ui-preconditions/${preconditionId}`,
    { method: "PATCH", body: data },
  );
}

export function deletePreconditionApi(preconditionId: string) {
  return request<ApiResponse<null>>(`/ui-preconditions/${preconditionId}`, {
    method: "DELETE",
  });
}

// ─── 试跑前置步骤（Task 8.2）─────────────────────────────────────────

export interface TestPreconditionParams {
  /** True = 成功后写 storage_state 到环境；False（默认）= 只跑不存 */
  persist_state?: boolean;
  /** 单条模板硬超时（秒）。5..600，默认 300（AI 登录瓶颈在 LLM
   *  inference，慢速模型每轮 30-60s，10 步可达 5-10 分钟）。
   *  scripted/cookie 类型会自然提前完成，可降到 30s。 */
  timeout_seconds?: number;
}

export function testPreconditionApi(
  envId: string,
  preconditionId: string,
  body: TestPreconditionParams = {},
) {
  return request<ApiResponse<TestPreconditionResult>>(
    `/ui-environments/${envId}/preconditions/${preconditionId}/test`,
    { method: "POST", body },
  );
}

// ─── UI 辅助：人类友好的 state 文件状态 ──────────────────────────────

export type StateHealth =
  | { kind: "never"; label: string }
  | { kind: "fresh"; label: string; saved_at: string }
  | { kind: "stale"; label: string; saved_at: string; days: number };

/**
 * 把 environment.state_saved_at 翻译成 UI 能直接渲染的健康状态：
 * - null → never（从未保存）
 * - ≤ 7 天 → fresh（新鲜，绿色）
 * - > 7 天 → stale（可能已过期，橙色；真实过期要等实际 navigate 才能知道）
 */
export function computeStateHealth(
  stateSavedAt: string | null,
  staleThresholdDays = 7,
): StateHealth {
  if (!stateSavedAt) return { kind: "never", label: "未保存登录态" };
  const savedMs = new Date(stateSavedAt).getTime();
  const days = Math.floor((Date.now() - savedMs) / 86_400_000);
  if (days >= staleThresholdDays) {
    return {
      kind: "stale",
      label: `登录态可能已过期（${days} 天前）`,
      saved_at: stateSavedAt,
      days,
    };
  }
  return {
    kind: "fresh",
    label: days === 0 ? "今日已保存登录态" : `${days} 天前保存`,
    saved_at: stateSavedAt,
  };
}

// ─── 前置步骤 type 的展示元信息 ──────────────────────────────────────

export interface PreconditionTypeMeta {
  label: string;
  description: string;
  icon: string;
  color: "default" | "info" | "success" | "warning" | "error";
}

export const PRECONDITION_TYPE_META: Record<
  PreconditionType,
  PreconditionTypeMeta
> = {
  state_inject: {
    label: "注入已保存的登录态",
    description: "从磁盘读取 storage_state（cookie + localStorage），过期自动降级到 AI 登录",
    icon: "i-carbon-data-refinery",
    color: "info",
  },
  ai_login: {
    label: "AI 智能登录",
    description: "LLM 自动操作登录表单；支持填用户名密码、解验证码、点击登录",
    icon: "i-carbon-bot",
    color: "success",
  },
  scripted_steps: {
    label: "确定性脚本步骤",
    description: "按顺序执行 Playwright 动作（goto / click / fill ...）；最快最稳，但要写死脚本",
    icon: "i-carbon-code",
    color: "warning",
  },
  cookie_inject: {
    label: "直接注入 Cookie",
    description: "把凭据里的 cookie 写进 BrowserContext；适合「已有 token / session」场景",
    icon: "i-carbon-cookie",
    color: "default",
  },
  http_login: {
    label: "API 直连登录（推荐）",
    description:
      "纯 HTTP 走「GET 拿挑战 cookie → POST 登录拿 token cookie → 注入浏览器」。" +
      "免浏览器免 LLM，<2s 完成，最稳。" +
      "适用于后台暴露 /auth/getCode + /auth/login 这类设计的网站。",
    icon: "i-carbon-api",
    color: "success",
  },
};

// ─── Task 10.1：执行触发弹窗 / 执行 API ───────────────────────────────
// 这一段类型与后端 ``schemas.ExecutionCreateRequest / ExecutionListItem /
// ExecutionDetailResponse`` 严格对齐。所有可选字段后端都会兜默认值，前端
// 只在用户明确改过时才下传，避免"默认 0/空字符串"覆盖后端默认。

export type ExecutionMode = "normal" | "debug";

export type ExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "stopped"
  | "aborted_budget";

export type CaseStatus = "pending" | "running" | "passed" | "failed" | "error" | "skipped";

export type DataConfidence = "reliable" | "synthesized" | "data_failure" | null;

export interface ExecutionCreateBody {
  testcase_ids: string[];
  environment_id?: string | null;
  mode?: ExecutionMode;
  llm_config_id?: string | null;
  loaded_set_ids?: string[];
  manual_overrides?: Record<string, unknown>;
  token_budget?: number | null;
  strict_data_mode?: boolean;
  chat_message_id?: string | null;
  /** 按 module_id 临时覆盖 module.entry_path（仅本次执行）。
   *  值可以是相对路径（``/admin/users``）或完整 URL；空串等同于"本次跑该模块时
   *  不带 entry_path"。后端按 ``module_id`` 字符串作为 key。 */
  module_entry_overrides?: Record<string, string>;
}

export interface PreflightModuleItem {
  module_id: string | null;
  module_name: string | null;
  entry_path: string | null;
  case_count: number;
}

export interface PreflightModulesResponse {
  items: PreflightModuleItem[];
}

export interface ExecutionListItem {
  id: string;
  project_id: string;
  environment_id: string | null;
  status: ExecutionStatus;
  mode: ExecutionMode;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  skipped_cases: number;
  /** 数据可信度三态计数（v3.0.1）：业务通过率把 data_failure_cases 从分母剔除 */
  reliable_cases: number;
  synthesized_cases: number;
  data_failure_cases: number;
  duration_ms: number | null;
  tokens_total: number;
  has_video: boolean;
  has_trace: boolean;
  triggered_by: string | null;
  chat_message_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutionStepResponse {
  id: string;
  case_result_id: string;
  step_number: number;
  description: string;
  expected_result: string | null;
  status: string;
  tool_calls: unknown[];
  ai_reasoning: string | null;
  snapshot_before: string | null;
  snapshot_after: string | null;
  assertion_passed: boolean | null;
  assertion_reason: string | null;
  assertion_evidence: string | null;
  screenshot_path: string | null;
  // nginx 直接出图的 web URL（``/uploads/ui_artifacts/...``）；老执行可能为空
  screenshot_url: string | null;
  error_message: string | null;
  retry_count: number;
  tokens_used: number;
  duration_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutionCaseResponse {
  id: string;
  execution_id: string;
  testcase_id: string | null;
  // 后端从 testcases / testcase_modules 表 join 来的元数据，方便测试报告
  // 直接展示"用例编号 / 标题 / 所属模块"而不是只有内部 UUID。
  // ``testcase_no`` 是项目内业务编号（int），前端渲染为 ``TC-0117`` 这种字
  // 符串。用例已被删除时这些字段为 null。
  testcase_no: number | null;
  testcase_title: string | null;
  testcase_module_id: string | null;
  testcase_module_name: string | null;
  status: CaseStatus;
  error_message: string | null;
  ai_summary: string | null;
  duration_ms: number | null;
  tokens_used: number;
  started_at: string | null;
  completed_at: string | null;
  sort_order: number;
  test_data_used: unknown[] | null;
  synthesized_data: unknown[];
  data_failures: unknown[];
  data_confidence: DataConfidence;
  /**
   * 后端字段名（``ExecutionCaseResponse.steps``）。注意：早期前端类型曾误写
   * 成 ``step_results``——保持与后端 schema 一致命名 ``steps``。
   */
  steps: ExecutionStepResponse[];
  created_at: string;
  updated_at: string;
}

export interface ExecutionDetailResponse extends ExecutionListItem {
  test_data_snapshot: Record<string, unknown> | null;
  config_snapshot: Record<string, unknown>;
  error_message: string | null;
  /** 生效的 token 预算上限（override > environment > 全局兜底） */
  effective_token_budget: number;
  /**
   * Nginx 静态视频路径（典型 `/uploads/ui_artifacts/<exec_id>/video/xxx.webm`）。
   *
   * **必须**用这个字段而非 `executionVideoUrl(id)` 给 `<video src>` —— HTML
   * media 元素发请求时不会自动带 `Authorization` header（axios interceptor
   * 不参与），鉴权 API 路径会 401，导致播放器显示"视频加载失败"。
   *
   * `executionVideoUrl(id)` 仍可用于"下载视频"按钮，浏览器跳转时会带 cookie
   * （如果有），且失败时用户能直接看到 401 提示。
   */
  video_url: string | null;
  trace_url: string | null;
  case_results: ExecutionCaseResponse[];
}

export interface ExecutionStopResponse {
  execution_id: string;
  prev_status: ExecutionStatus;
  status: ExecutionStatus;
  already_terminal: boolean;
}

/** 执行配置弹窗"复用上次"返回的快照。null = 该项目从未跑过。 */
export interface RecentExecutionConfig {
  testcase_ids: string[];
  environment_id?: string | null;
  loaded_set_ids: string[];
  manual_overrides: Record<string, unknown>;
  llm_config_id: string | null;
  token_budget_override: number | null;
  strict_data_mode: boolean;
  mode: ExecutionMode;
}

// ─── 执行 API ─────────────────────────────────────────────────────────

export function createExecutionApi(projectId: string, body: ExecutionCreateBody) {
  return request<ApiResponse<ExecutionListItem>>(
    `/projects/${projectId}/ui-executions`,
    { method: "POST", body },
  );
}

// ─── Phase 13 / Task 13.3 — chat ConfirmationCard 派发 ───────────────

/**
 * "确认执行"按钮调用：仅传 `plan_id`，由后端根据缓存的 plan 反查
 * `testcase_ids / environment_id / llm_config_id`，避免用户在前端篡改字段
 * 冒充已确认 plan（设计 §10.3.3 安全闸门）。
 *
 * `triggered_chat_session_id` 用于执行完成时把 ✅ 系统消息回流到该会话末尾；
 * `source` 固定 `chat` 用于历史筛选 + 防 adhoc 污染概览统计。
 */
export interface ConfirmExecutionPlanBody {
  plan_id: string;
  triggered_chat_session_id: string;
  /** strict 强度下用户输入的挑战短语（M2 task 13.5 启用，M1 留空即可）。 */
  challenge_value?: string;
  /** strict 强度下用户勾选"我已知晓"。M1 留空。 */
  ack?: boolean;
}

export function confirmExecutionPlanApi(
  projectId: string,
  body: ConfirmExecutionPlanBody,
) {
  // 后端 `ExecutionCreateRequest` 通过 plan_id 反查；不送 testcase_ids 是
  // 故意的——后端会从缓存还原。前端绝不在此处发任何用例 / 环境字段。
  return request<ApiResponse<ExecutionListItem>>(
    `/projects/${projectId}/ui-executions`,
    {
      method: "POST",
      body: {
        plan_id: body.plan_id,
        triggered_chat_session_id: body.triggered_chat_session_id,
        source: "chat",
        // testcase_ids 留空 array，后端按 plan_id 还原
        testcase_ids: [],
      },
    },
  );
}

/** 拉取本批次涉及的模块列表（含 entry_path）。给执行配置弹窗的"测试地址"区段用。 */
export function preflightModulesApi(
  projectId: string,
  testcaseIds: string[],
) {
  return request<ApiResponse<PreflightModulesResponse>>(
    `/projects/${projectId}/ui-executions/preflight-modules`,
    { method: "POST", body: { testcase_ids: testcaseIds } },
  );
}

export function listExecutionsApi(
  projectId: string,
  params: { page?: number; page_size?: number; status?: ExecutionStatus } = {},
) {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  if (params.status) q.set("status", params.status);
  const qs = q.toString();
  return request<
    ApiResponse<{
      items: ExecutionListItem[];
      total: number;
      page: number;
      page_size: number;
    }>
  >(`/projects/${projectId}/ui-executions${qs ? `?${qs}` : ""}`);
}

export function getExecutionApi(executionId: string) {
  return request<ApiResponse<ExecutionDetailResponse>>(
    `/ui-executions/${executionId}`,
  );
}

export function stopExecutionApi(executionId: string) {
  return request<ApiResponse<ExecutionStopResponse>>(
    `/ui-executions/${executionId}/stop`,
    { method: "POST" },
  );
}

/**
 * 硬删除一次执行 + 关联磁盘 artifact（video / trace / step screenshot）。
 *
 * 业务约束：
 * * 后端要求 execution 必须是终态（completed / stopped / failed / aborted_budget），
 *   running / pending 会返回 409 + 提示用户先停止。前端按钮在非终态下应 disable。
 * * 不可恢复 —— DB 行 + 磁盘文件全删。前端务必加二次确认弹窗。
 * * 权限复用 ``UI_EXEC_STOP``：能停就能删。
 */
export interface ExecutionDeleteResponse {
  execution_id: string;
  deleted: boolean;
  files_deleted: number;
  file_errors: string[];
}

export function deleteExecutionApi(executionId: string) {
  return request<ApiResponse<ExecutionDeleteResponse>>(
    `/ui-executions/${executionId}`,
    { method: "DELETE" },
  );
}

/** Task 9.7：debug 模式下推进 step_paused → 下一步。幂等。 */
export interface ExecutionContinueResponse {
  execution_id: string;
  signal_delivered: boolean;
  status: ExecutionStatus;
}

export function continueExecutionApi(executionId: string) {
  return request<ApiResponse<ExecutionContinueResponse>>(
    `/ui-executions/${executionId}/continue`,
    { method: "POST" },
  );
}

/**
 * 失败用例重跑（Task 9.6）。从原 execution 抽 status in (failed/error/skipped)
 * 的用例 + 复用原 config_snapshot 跑一次新的；body 字段全部可选。
 */
export interface ExecutionRetryBody {
  environment_id?: string | null;
  llm_config_id?: string | null;
  token_budget?: number | null;
  strict_data_mode?: boolean;
  extra_loaded_set_ids?: string[];
  extra_manual_overrides?: Record<string, unknown>;
}

export function retryFailedExecutionApi(
  executionId: string,
  body: ExecutionRetryBody = {},
) {
  return request<ApiResponse<ExecutionListItem>>(
    `/ui-executions/${executionId}/retry-failed`,
    { method: "POST", body },
  );
}

/** 视频 / Trace 下载链接（直接给 <a href=> 用；带 cookie/header 由浏览器处理）。 */
export function executionVideoUrl(executionId: string): string {
  return `/api/ui-executions/${executionId}/video`;
}

export function executionTraceUrl(executionId: string): string {
  return `/api/ui-executions/${executionId}/trace`;
}

/** 获取 SSE 重放端点 URL —— 监控页据此切到"按时间轴重看历史"模式。 */
export function executionReplayStreamUrl(
  executionId: string,
  opts: { interStepDelaySeconds?: number; interCaseDelaySeconds?: number } = {},
): string {
  const params = new URLSearchParams();
  if (opts.interStepDelaySeconds && opts.interStepDelaySeconds > 0) {
    params.set("inter_step_delay_seconds", String(opts.interStepDelaySeconds));
  }
  if (opts.interCaseDelaySeconds && opts.interCaseDelaySeconds > 0) {
    params.set("inter_case_delay_seconds", String(opts.interCaseDelaySeconds));
  }
  const qs = params.toString();
  return `/api/ui-executions/${executionId}/replay${qs ? `?${qs}` : ""}`;
}

/**
 * 复用上次：返回精确匹配该 testcase 组合的最近一次配置；
 * 若不匹配则降级返回该项目最近一次任意配置；从未跑过返回 null。
 *
 * 顺序无关：服务端按集合相等比较。
 */
export function getRecentConfigApi(
  projectId: string,
  testcaseIds: string[] | undefined,
) {
  const qs = new URLSearchParams();
  (testcaseIds ?? []).forEach((id) => qs.append("testcase_ids", id));
  const query = qs.toString();
  return request<ApiResponse<{ config: RecentExecutionConfig | null }>>(
    `/projects/${projectId}/recent-executions/last-config${
      query ? `?${query}` : ""
    }`,
  );
}

// ─── 物料合并预览 + 缺料预检（test_data 模块端点，归口在 uiAutomation 因
//     这两处只服务于"执行触发弹窗"场景；放 testData.ts 反而割裂语义）─────

export interface MergePreviewRequest {
  set_ids?: string[];
  environment_id?: string | null;
  testcase_ids?: string[];
  manual_overrides?: Record<string, unknown>;
}

/**
 * 合并预览中"该 key 来自哪个物料集 / 哪一层"的单条溯源记录。
 *
 * 后端按合并顺序追加（personal → project → environment → loaded → manual），
 * 数组里**最后一条 = 胜出值**，其它条 ``overridden=true``。同一个 key 在多
 * 个集合里都有时，前端可以展开显示全部候选——之前用户反馈"多个物料集都有
 * username，合并明细只展示一条 username"，缺的就是这块溯源信息。
 *
 * secret 永远只输出 ``●●●●`` / ``has_secret_value=true``，不会带明文。
 */
export interface MergeSource {
  set_id: string | null;
  set_name: string;
  scope: "personal" | "project" | "environment" | "loaded" | "testcase" | "manual";
  display_value: string;
  has_secret_value: boolean;
  file_name: string | null;
  overridden: boolean;
}

export interface MergedItem {
  key: string;
  value_type: string;
  description: string | null;
  display_value: string;
  has_secret_value: boolean;
  file_name: string | null;
  synthetic_source: string | null;
  /** 推荐展示的"来源"标签：哪一层物料覆盖了它。前端兜底；后端没传时按
   *  manual_overrides → loaded → environment → personal → project → 无 推断 */
  source?:
    | "manual_override"
    | "loaded_set"
    | "environment"
    | "personal"
    | "project"
    | "synthesized"
    | "unknown";
  /** 该 key 在合并链中出现过的全部候选来源（按层级追加，最后一条胜出）。
   *  为空 = ad-hoc / 自造数据，没有真实物料集来源。后端 < 2026-05 的部署
   *  返回此字段 ``undefined``；前端兜底按空数组处理。 */
  sources?: MergeSource[];
}

export interface MergePreviewResponse {
  items: MergedItem[];
}

export interface MissingStepRef {
  testcase_id: string;
  step_number: number;
  where: "action" | "expected" | string;
}

export interface MissingAlert {
  key: string;
  detected_in_steps: MissingStepRef[];
  will_synthesize: boolean;
}

export interface MissingCheckResponse {
  missing_keys: string[];
  will_synthesize: boolean;
  details: MissingAlert[];
}

/**
 * 执行配置弹窗"物料合并预览"。请求体所有字段可空——空 body 也合法，结果
 * 即"项目默认 + 个人 + 环境绑定"的合并。secret 不会以明文返回。
 */
export function previewMergeApi(
  projectId: string,
  body: MergePreviewRequest,
) {
  return request<ApiResponse<MergePreviewResponse>>(
    `/projects/${projectId}/test-data/preview-merge`,
    { method: "POST", body },
  );
}

/**
 * 执行配置弹窗"缺料预检"（非阻断，仅警告）。
 * - 默认 will_synthesize=true：AI 会用 platform_synthesize_data 兜底
 * - 严格模式下用户勾了 ``strict_data_mode``，前端用 ``missing_keys.length > 0``
 *   置灰执行按钮；本接口不感知 strict 模式
 */
export function missingCheckApi(
  projectId: string,
  body: MergePreviewRequest,
) {
  return request<ApiResponse<MissingCheckResponse>>(
    `/projects/${projectId}/test-data/missing-check`,
    { method: "POST", body },
  );
}

// ─── UI 辅助：状态徽章映射 ────────────────────────────────────────────

export const EXECUTION_MODE_META: Record<
  ExecutionMode,
  { label: string; color: "default" | "info"; description: string }
> = {
  normal: {
    label: "正常",
    color: "default",
    description: "AI 全自动跑完所有用例，进度通过 SSE 实时推送",
  },
  debug: {
    label: "调试",
    color: "info",
    description: "每步暂停等候 ▶ 继续；30 分钟无操作自动停止",
  },
};
