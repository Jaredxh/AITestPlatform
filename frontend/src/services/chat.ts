import { request } from "./request";
import type { ApiResponse } from "./auth";

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tokens_used: number | null;
  model_used: string | null;
  meta_data: Record<string, unknown> | null;
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
