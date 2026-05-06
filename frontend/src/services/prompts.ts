import { request } from "./request";
import type { ApiResponse } from "./auth";

export interface PromptVariable {
  name: string;
  label: string;
  source: "context" | "auto" | "manual";
}

export interface PromptInfo {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  content: string;
  category: string;
  sub_category: string | null;
  is_system: boolean;
  is_default: boolean;
  auto_apply: boolean;
  variables: PromptVariable[];
  version: number;
  created_by: string;
  creator_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface PromptListItem {
  id: string;
  name: string;
  description: string | null;
  category: string;
  sub_category: string | null;
  is_system: boolean;
  is_default: boolean;
  auto_apply: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PromptVersion {
  id: string;
  template_id: string;
  version: number;
  content: string;
  change_note: string | null;
  created_by: string;
  creator_name: string | null;
  created_at: string;
}

export interface PromptCreateParams {
  name: string;
  description?: string;
  content: string;
  category: string;
  sub_category?: string;
  is_default?: boolean;
  auto_apply?: boolean;
  variables?: PromptVariable[];
}

export interface PromptUpdateParams {
  name?: string;
  description?: string;
  content?: string;
  category?: string;
  sub_category?: string;
  is_default?: boolean;
  auto_apply?: boolean;
  variables?: PromptVariable[];
  change_note?: string;
}

export function getPromptsApi(projectId: string, category?: string) {
  const qs = category ? `?category=${category}` : "";
  return request<ApiResponse<PromptListItem[]>>(
    `/projects/${projectId}/prompts${qs}`,
  );
}

export function getPromptDetailApi(promptId: string) {
  return request<ApiResponse<PromptInfo>>(`/prompts/${promptId}`);
}

export function createPromptApi(projectId: string, data: PromptCreateParams) {
  return request<ApiResponse<PromptInfo>>(`/projects/${projectId}/prompts`, {
    method: "POST",
    body: data,
  });
}

export function updatePromptApi(promptId: string, data: PromptUpdateParams) {
  return request<ApiResponse<PromptInfo>>(`/prompts/${promptId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deletePromptApi(promptId: string) {
  return request<ApiResponse<null>>(`/prompts/${promptId}`, {
    method: "DELETE",
  });
}

export function getPromptVersionsApi(promptId: string) {
  return request<ApiResponse<PromptVersion[]>>(`/prompts/${promptId}/versions`);
}

export function setPromptDefaultApi(promptId: string) {
  return request<ApiResponse<PromptInfo>>(`/prompts/${promptId}/set-default`, {
    method: "POST",
  });
}

export function initProjectPromptsApi(projectId: string) {
  return request<ApiResponse<{ created_count: number }>>(
    `/projects/${projectId}/prompts/init`,
    { method: "POST" },
  );
}
