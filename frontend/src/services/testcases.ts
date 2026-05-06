import { request } from "./request";
import type { ApiResponse } from "./auth";

// ── Module types ──

export interface ModuleTreeNode {
  id: string;
  name: string;
  parent_id: string | null;
  order_index: number;
  /** 模块入口路径（可选）。例：``/admin/users``、``/dashboard/stats``；
   *  也可以填完整 URL 跨子域。留空 → 由用例步骤自然语言决定目标地址。 */
  entry_path: string | null;
  case_count: number;
  children: ModuleTreeNode[];
}

export interface ModuleInfo {
  id: string;
  project_id: string;
  parent_id: string | null;
  name: string;
  order_index: number;
  entry_path: string | null;
  created_at: string;
  updated_at: string;
}

// ── Testcase types ──

export interface TestcaseStep {
  id?: string;
  testcase_id?: string;
  step_number: number;
  action: string;
  expected_result: string | null;
  created_at?: string;
}

export type ExecResult = "not_run" | "passed" | "failed" | "blocked";

export interface TestcaseListItem {
  id: string;
  case_no: number;
  display_id: string;
  module_id: string | null;
  module_name: string | null;
  title: string;
  priority: string;
  status: string;
  source: string;
  exec_result: ExecResult;
  creator_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestcaseDetail {
  id: string;
  case_no: number;
  display_id: string;
  project_id: string;
  module_id: string | null;
  module_name: string | null;
  title: string;
  precondition: string | null;
  priority: string;
  status: string;
  source: string;
  exec_result: ExecResult;
  created_by: string;
  creator_name: string | null;
  steps: TestcaseStep[];
  /** Task 8.5 新增：该用例默认加载的物料集 id。Task 9.1 执行时合并 */
  default_data_set_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface PaginatedTestcases {
  items: TestcaseListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface TestcaseCreateParams {
  title: string;
  module_id?: string | null;
  precondition?: string | null;
  priority?: string;
  steps?: { step_number: number; action: string; expected_result?: string | null }[];
  default_data_set_ids?: string[];
}

export interface TestcaseUpdateParams {
  title?: string;
  module_id?: string | null;
  precondition?: string | null;
  priority?: string;
  status?: string;
  exec_result?: ExecResult;
  steps?: { step_number: number; action: string; expected_result?: string | null }[];
  /** 传空数组 = 清空；传 undefined = 不改 */
  default_data_set_ids?: string[];
}

// ── AI generation types ──

export interface GenerateParams {
  document_id: string;
  module_id?: string | null;
  llm_config_id?: string | null;
}

export interface GeneratedTestcase {
  title: string;
  precondition?: string | null;
  priority?: string;
  steps: { step_number: number; action: string; expected_result?: string | null }[];
}

export interface BatchAcceptParams {
  batch_id: string;
  testcases: GeneratedTestcase[];
  module_id?: string | null;
}

export interface BatchAcceptResult {
  accepted_count: number;
  batch_id: string;
}

export interface GenerationBatchInfo {
  id: string;
  project_id: string;
  document_id: string | null;
  module_id: string | null;
  model_used: string | null;
  status: "generating" | "completed" | "failed";
  generated_count: number;
  accepted_count: number;
  generation_time_ms: number | null;
  created_at: string;
  document_name: string | null;
  module_name: string | null;
  testcases: GeneratedTestcase[];
}

// ── Module APIs ──

export function getModuleTreeApi(projectId: string) {
  return request<ApiResponse<ModuleTreeNode[]>>(
    `/testcases/projects/${projectId}/modules`,
  );
}

export function createModuleApi(
  projectId: string,
  data: {
    name: string;
    parent_id?: string | null;
    order_index?: number;
    entry_path?: string | null;
  },
) {
  return request<ApiResponse<ModuleInfo>>(
    `/testcases/projects/${projectId}/modules`,
    { method: "POST", body: data },
  );
}

export function updateModuleApi(
  moduleId: string,
  data: {
    name?: string;
    parent_id?: string | null;
    order_index?: number;
    /** 注意：``entry_path`` 显式 null 表示"清空入口路径"；不传该字段则保留原值。
     *  字符串自动 trim；空串等价于 null。 */
    entry_path?: string | null;
  },
) {
  return request<ApiResponse<ModuleInfo>>(`/testcases/modules/${moduleId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteModuleApi(moduleId: string) {
  return request<ApiResponse<null>>(`/testcases/modules/${moduleId}`, {
    method: "DELETE",
  });
}

// ── Testcase APIs ──

export function listTestcasesApi(
  projectId: string,
  params?: {
    page?: number;
    page_size?: number;
    module_id?: string;
    priority?: string;
    status?: string;
    source?: string;
    exec_result?: string;
    search?: string;
  },
) {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  if (params?.module_id) qs.set("module_id", params.module_id);
  if (params?.priority) qs.set("priority", params.priority);
  if (params?.status) qs.set("status", params.status);
  if (params?.source) qs.set("source", params.source);
  if (params?.exec_result) qs.set("exec_result", params.exec_result);
  if (params?.search) qs.set("search", params.search);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return request<ApiResponse<PaginatedTestcases>>(
    `/testcases/projects/${projectId}/cases${query}`,
  );
}

export function getTestcaseApi(testcaseId: string) {
  return request<ApiResponse<TestcaseDetail>>(
    `/testcases/cases/${testcaseId}`,
  );
}

export function createTestcaseApi(
  projectId: string,
  data: TestcaseCreateParams,
) {
  return request<ApiResponse<TestcaseDetail>>(
    `/testcases/projects/${projectId}/cases`,
    { method: "POST", body: data },
  );
}

export function updateTestcaseApi(
  testcaseId: string,
  data: TestcaseUpdateParams,
) {
  return request<ApiResponse<TestcaseDetail>>(
    `/testcases/cases/${testcaseId}`,
    { method: "PATCH", body: data },
  );
}

export function deleteTestcaseApi(testcaseId: string) {
  return request<ApiResponse<null>>(`/testcases/cases/${testcaseId}`, {
    method: "DELETE",
  });
}

// ── AI generation APIs ──

export function batchAcceptApi(data: BatchAcceptParams) {
  return request<ApiResponse<BatchAcceptResult>>(`/testcases/batch-accept`, {
    method: "POST",
    body: data,
  });
}

export function listGenerationBatchesApi(projectId: string) {
  return request<ApiResponse<GenerationBatchInfo[]>>(
    `/testcases/projects/${projectId}/generation-batches`,
  );
}

export function startGenerationTaskApi(projectId: string, data: GenerateParams) {
  return request<ApiResponse<GenerationBatchInfo>>(
    `/testcases/projects/${projectId}/generate-task`,
    { method: "POST", body: data },
  );
}

export function getGenerationBatchApi(batchId: string) {
  return request<ApiResponse<GenerationBatchInfo>>(
    `/testcases/generation-batches/${batchId}`,
  );
}

/**
 * 强制结束 AI 生成任务（无论是真在跑还是卡死的孤儿任务）。
 * 后端会把 batch 标 failed，并往 in-process stream hub 发 done，
 * 让所有订阅 SSE 的前端立刻断流并进入"已结束"视图。
 */
export interface CancelGenerationResult {
  batch_id: string;
  status: string;
  already_done: boolean;
  reason: string;
}

export function cancelGenerationBatchApi(batchId: string) {
  return request<ApiResponse<CancelGenerationResult>>(
    `/testcases/generation-batches/${batchId}/cancel`,
    { method: "POST" },
  );
}
