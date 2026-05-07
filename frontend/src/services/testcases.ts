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

// ── Excel 导入 / 导出 ──

/**
 * 单行导入错误。row 是 Excel 行号（含表头算 1，所以数据从 2 开始）。
 */
export interface TestcaseImportError {
  row: number;
  message: string;
  title?: string | null;
}

/**
 * 批量导入回执：created/updated/skipped 各自累计；行级错误聚合在 errors。
 * created_modules 是按 path 自动建出的新模块（用于提示用户 "导入还顺手建了
 * X 个模块"，方便事后核对）。
 */
export interface TestcaseImportReport {
  total: number;
  created: number;
  updated: number;
  skipped: number;
  created_modules: string[];
  errors: TestcaseImportError[];
}

/**
 * 在浏览器里触发 a[download] 自动下载二进制 blob。抽出来给"模板下载 / 导出"
 * 两条路径复用，避免每次都手撸一遍 ObjectURL 生命周期。
 */
function triggerDownload(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 大 blob 立即 revoke 避免 Edge 上偶发"下载文件大小为 0"
  setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

/**
 * 把后端 Content-Disposition 里的 ``filename*=UTF-8''xxx`` 还原成可用的中文
 * 文件名。失败时回退到默认名，避免下载失败。
 */
function parseDownloadFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      /* fallthrough */
    }
  }
  const plain = disposition.match(/filename="?([^";]+)"?/i);
  return plain?.[1] || fallback;
}

/**
 * 二进制下载的统一通道。不复用 ofetch 的 ``request``——它会按 JSON 解析响应
 * 体，对 xlsx 是有损操作。这里用裸 fetch + Authorization 头，自己控全 blob 流。
 */
async function downloadBinary(
  url: string,
  fallbackName: string,
): Promise<{ filename: string }> {
  const token = localStorage.getItem("access_token") || "";
  const res = await fetch(`/api${url}`, {
    method: "GET",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    // 后端发生业务错误时仍是 application/json，把 message 拎出来抛给上层
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const body = await res.json().catch(() => null);
      const msg = (body && (body.message || body.detail)) || `下载失败（${res.status}）`;
      throw new Error(msg);
    }
    throw new Error(`下载失败（${res.status}）`);
  }
  const filename = parseDownloadFilename(res.headers.get("content-disposition"), fallbackName);
  const blob = await res.blob();
  triggerDownload(blob, filename);
  return { filename };
}

/**
 * 下载用例导入模板（含示例 + 字段说明 sheet）。
 */
export function downloadTestcaseTemplateApi(projectId: string) {
  return downloadBinary(
    `/testcases/projects/${projectId}/cases/template`,
    "testcases-template.xlsx",
  );
}

/**
 * 按当前筛选条件导出全部用例（不分页）为 xlsx。筛选参数与列表保持一致，
 * 让"页面看到什么 → 导出什么"。
 */
export function exportTestcasesApi(
  projectId: string,
  params?: {
    module_id?: string;
    priority?: string;
    status?: string;
    source?: string;
    exec_result?: string;
    search?: string;
  },
) {
  const qs = new URLSearchParams();
  if (params?.module_id) qs.set("module_id", params.module_id);
  if (params?.priority) qs.set("priority", params.priority);
  if (params?.status) qs.set("status", params.status);
  if (params?.source) qs.set("source", params.source);
  if (params?.exec_result) qs.set("exec_result", params.exec_result);
  if (params?.search) qs.set("search", params.search);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return downloadBinary(
    `/testcases/projects/${projectId}/cases/export${query}`,
    "testcases.xlsx",
  );
}

/**
 * 上传 xlsx 文件批量导入用例。单文件 ≤ 10MB。
 */
export function importTestcasesApi(projectId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ApiResponse<TestcaseImportReport>>(
    `/testcases/projects/${projectId}/cases/import`,
    { method: "POST", body: form },
  );
}
