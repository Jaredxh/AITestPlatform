import { request } from "./request";
import type { ApiResponse } from "./auth";

/**
 * 消息渲染类别（Phase 13 / Task 13.0）。
 *
 * - `normal`：用户/LLM 普通文本（默认；老消息行落到此分支保持向前兼容）
 * - `skill_card`：LLM 调 propose_execution_plan 返回的 ConfirmationCard payload
 *                 （task 13.3 渲染为新组件 ConfirmationCard.vue）
 * - `task_badge`：用户确认派发后立即落库的任务卡（task 13.3 渲染为 TaskBadge.vue）
 * - `execution_event`：后端 system_event_service 异步推送的完成态结构化结果
 *                     （task 13.3 渲染为 ExecutionEventCard.vue）
 *
 * 前端"未识别 kind"统一兜底走 normal 渲染分支，保证后端先发布、前端后升级
 * 的发布顺序下不会白屏。
 */
export type ChatMessageKind =
  | "normal"
  | "skill_card"
  | "task_badge"
  | "execution_event";

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tokens_used: number | null;
  model_used: string | null;
  meta_data: Record<string, unknown> | null;
  /** Phase 12 / Task 12.6 — 该消息触发的 skill 调用日志 id（前端徽章定位用）。 */
  skill_invocation_id?: string | null;
  /** Phase 13 / Task 13.0 — 消息渲染类别；缺失时按 `normal` 处理。 */
  kind?: ChatMessageKind;
  created_at: string;
}

export interface ChatSession {
  id: string;
  user_id: string;
  project_id: string | null;
  title: string | null;
  llm_config_id: string | null;
  llm_config_name: string | null;
  system_prompt: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[];
}

export interface FileUploadResult {
  type: "document" | "image";
  filename: string;
  content_type: string;
  text: string | null;
  image_base64: string | null;
  size: number;
}

export function getSessionsApi(projectId?: string) {
  const qs = projectId ? `?project_id=${projectId}` : "";
  return request<ApiResponse<ChatSession[]>>(`/chat/sessions${qs}`);
}

export function createSessionApi(data: {
  title?: string;
  llm_config_id?: string;
  project_id?: string;
  system_prompt?: string;
}) {
  return request<ApiResponse<ChatSession>>("/chat/sessions", {
    method: "POST",
    body: data,
  });
}

export function getSessionDetailApi(sessionId: string) {
  return request<ApiResponse<ChatSessionDetail>>(`/chat/sessions/${sessionId}`);
}

export function updateSessionApi(
  sessionId: string,
  data: { title?: string; llm_config_id?: string; system_prompt?: string },
) {
  return request<ApiResponse<ChatSession>>(`/chat/sessions/${sessionId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteSessionApi(sessionId: string) {
  return request<ApiResponse<null>>(`/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export function getMessagesApi(sessionId: string) {
  return request<ApiResponse<ChatMessage[]>>(
    `/chat/sessions/${sessionId}/messages`,
  );
}

export function uploadFileApi(file: File): Promise<ApiResponse<FileUploadResult>> {
  const formData = new FormData();
  formData.append("file", file);
  return request<ApiResponse<FileUploadResult>>("/chat/upload", {
    method: "POST",
    body: formData,
  });
}

export interface StartChatTaskResponse {
  user_message_id: string;
  assistant_message_id: string;
}

/**
 * 发起一轮对话：立刻返回 user_message_id + assistant_message_id。
 * 真正的生成过程跑在后台，前端需要再调 subscribe 才能拿到流；
 * 刷新 / 切页 / 断网都不会打断后台任务。
 */
export function startChatTaskApi(
  sessionId: string,
  data: { content: string; llm_config_id?: string },
) {
  return request<ApiResponse<StartChatTaskResponse>>(
    `/chat/sessions/${sessionId}/send`,
    { method: "POST", body: data },
  );
}

// ─── Phase 13 / Task 13.3 — 系统事件 SSE & 离线汇总 ────────────────────

/**
 * 单条"你离开期间完成的任务"汇总项，对应一条 `kind=execution_event` 消息。
 * 前端首屏渲染顶部 banner 时使用；点击展开能列出全部 N 条任务。
 */
export interface PendingTaskSummaryItem {
  message_id: string;
  task_id: string | null;
  result: Record<string, unknown>;
  content: string;
  created_at: string | null;
}

export interface PendingTaskSummary {
  session_id: string;
  count: number;
  items: PendingTaskSummaryItem[];
}

/**
 * 首屏顶部"你离开期间完成 N 个任务"汇总卡数据源。后端扫该会话末尾最近 20
 * 条 `kind='execution_event'` 消息——已落库即被持久化，浏览器重连/重启不影
 * 响。`count==0` 时前端不渲染汇总卡。
 */
export function getPendingTaskSummaryApi(sessionId: string) {
  return request<ApiResponse<PendingTaskSummary>>(
    `/chat/sessions/${sessionId}/pending-task-summary`,
  );
}

/**
 * 拼一个会话级 system-events SSE URL（前端用 `EventSource` 订阅）。这条流
 * 与 `/messages/{id}/stream` 解耦，跟随整个 session 的生命周期接收异步事件
 * （skill_card / task_status / execution_event）。
 *
 * **必须**带 cookie/token 鉴权——`EventSource` 的 `withCredentials` 默认
 * 仅发同源 cookie；如果系统是 Bearer token 鉴权，前端需要走 fetch + reader
 * 自己解析 SSE。本项目目前用 cookie 模式，直接 EventSource 即可。
 */
export function chatSystemEventsUrl(sessionId: string): string {
  return `/api/chat/sessions/${sessionId}/system-events`;
}
