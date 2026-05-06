import { request } from "./request";
import type { ApiResponse } from "./auth";

export interface LLMConfigInfo {
  id: string;
  name: string;
  provider: string;
  model: string;
  base_url: string | null;
  temperature: number;
  max_tokens: number;
  is_default: boolean;
  has_api_key: boolean;
  created_by: string;
  creator_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface LLMConfigCreateParams {
  name: string;
  provider: string;
  model: string;
  api_key?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number;
  is_default?: boolean;
}

export interface LLMConfigUpdateParams {
  name?: string;
  provider?: string;
  model?: string;
  api_key?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number;
  is_default?: boolean;
}

export interface LLMTestResult {
  success: boolean;
  message: string;
  model: string | null;
  response_time_ms: number | null;
}

export function getLLMConfigsApi() {
  return request<ApiResponse<LLMConfigInfo[]>>("/llm-configs");
}

export function getLLMConfigApi(configId: string) {
  return request<ApiResponse<LLMConfigInfo>>(`/llm-configs/${configId}`);
}

export function createLLMConfigApi(data: LLMConfigCreateParams) {
  return request<ApiResponse<LLMConfigInfo>>("/llm-configs", {
    method: "POST",
    body: data,
  });
}

export function updateLLMConfigApi(configId: string, data: LLMConfigUpdateParams) {
  return request<ApiResponse<LLMConfigInfo>>(`/llm-configs/${configId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteLLMConfigApi(configId: string) {
  return request<ApiResponse<null>>(`/llm-configs/${configId}`, { method: "DELETE" });
}

export function testSavedConfigApi(configId: string) {
  return request<ApiResponse<LLMTestResult>>(`/llm-configs/${configId}/test`, {
    method: "POST",
  });
}

export function testNewConfigApi(data: {
  provider: string;
  model: string;
  api_key?: string;
  base_url?: string;
}) {
  return request<ApiResponse<LLMTestResult>>("/llm-configs/test", {
    method: "POST",
    body: data,
  });
}
