import { request } from "./request";
import type { ApiResponse } from "./auth";

// ─── Type definitions ────────────────────────────────────────────────────────

export type SkillActivationMode = "manual" | "trigger" | "agent_callable" | "always" | "auto_apply";
export type SkillSource = "built_in" | "imported" | "custom";
export type SkillSafetyStatus = "unscanned" | "clean" | "warning" | "blocked";

export interface SkillListItem {
  id: string;
  project_id: string | null;
  name: string;
  slug: string;
  description: string;
  semantic_version: string;
  category: string;
  tags: string[];
  triggers: string[];
  activation_mode: SkillActivationMode;
  source: SkillSource;
  is_enabled: boolean;
  safety_scan_status: SkillSafetyStatus;
  db_version: number;
  created_at: string;
  updated_at: string;
}

export interface SkillAttachment {
  path: string;
  size: number;
}

export interface SkillSafetyFinding {
  type: string;
  severity: string;
  snippet: string;
  line?: number | null;
}

export interface SkillDetail extends SkillListItem {
  body: string;
  metadata: Record<string, unknown>;
  attachments: SkillAttachment[];
  source_url: string | null;
  safety_scan_notes: string | null;
  tools_required: string[];
  created_by: string;
}

export interface SkillVersion {
  id: string;
  skill_id: string;
  db_version: number;
  body: string;
  metadata: Record<string, unknown>;
  change_note: string | null;
  created_by: string;
  created_at: string;
}

export interface SkillImportPreview {
  name: string;
  slug: string;
  description: string;
  semantic_version: string;
  category: string;
  activation_mode: SkillActivationMode;
  triggers: string[];
  tools_required: string[];
  body_preview: string;
  body_size_bytes: number;
  attachments: SkillAttachment[];
  safety_status: SkillSafetyStatus;
  safety_findings: SkillSafetyFinding[];
  metadata_extra_keys: string[];
  skill_id: string | null;
}

export interface SkillCreateParams {
  name: string;
  slug: string;
  description: string;
  semantic_version?: string;
  category?: string;
  tags?: string[];
  triggers?: string[];
  tools_required?: string[];
  activation_mode?: SkillActivationMode;
  body: string;
  metadata?: Record<string, unknown>;
  attachments?: SkillAttachment[];
}

export interface SkillUpdateParams {
  name?: string;
  description?: string;
  semantic_version?: string;
  category?: string;
  tags?: string[];
  triggers?: string[];
  tools_required?: string[];
  activation_mode?: SkillActivationMode;
  body?: string;
  metadata?: Record<string, unknown>;
  attachments?: SkillAttachment[];
  is_enabled?: boolean;
  change_note?: string;
}

export interface SkillListQuery {
  page?: number;
  page_size?: number;
  activation_mode?: SkillActivationMode | "";
  source?: SkillSource | "";
  is_enabled?: boolean | null;
  search?: string;
}

export interface SkillListResponse {
  items: SkillListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SkillUsageStatsItem {
  count: number;
  success_rate: number;
  avg_tokens: number;
}

export interface SkillUsageStats {
  [skillId: string]: SkillUsageStatsItem;
}

export interface SkillUsageTrendPoint {
  date: string;
  count: number;
}

export interface SkillUsageTrend {
  [skillId: string]: SkillUsageTrendPoint[];
}

export interface SkillUsageFailure {
  id: string;
  session_id: string | null;
  message_id: string | null;
  activation_reason: string;
  outcome: string;
  error_message: string | null;
  created_at: string | null;
}

/** Task 12.6 — SSE 事件 / 消息上携带的"激活信号"通用字段。 */
export type SkillActivationReason =
  | "always"
  | "manual"
  | "trigger_match"
  | "agent_callable"
  | "auto_apply";

export interface SkillActivatedEvent {
  skill_id: string;
  slug: string;
  name: string;
  activation_reason: SkillActivationReason;
  matched_trigger?: string | null;
}

export interface SkillTriggerMatch {
  skill_id: string;
  name: string;
  slug: string;
  score: number;
  matched_triggers: string[];
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of entries) sp.append(k, String(v));
  return `?${sp.toString()}`;
}

// ─── API methods ─────────────────────────────────────────────────────────────

export function listSkillsApi(projectId: string, query: SkillListQuery = {}) {
  const qs = buildQuery({
    page: query.page ?? 1,
    page_size: query.page_size ?? 100,
    activation_mode: query.activation_mode ?? "",
    source: query.source ?? "",
    is_enabled: query.is_enabled === null || query.is_enabled === undefined ? "" : query.is_enabled,
    search: query.search ?? "",
  });
  return request<ApiResponse<SkillListResponse>>(`/projects/${projectId}/skills${qs}`);
}

export function getSkillApi(skillId: string) {
  return request<ApiResponse<SkillDetail>>(`/skills/${skillId}`);
}

export function listSkillVersionsApi(skillId: string) {
  return request<ApiResponse<SkillVersion[]>>(`/skills/${skillId}/versions`);
}

export function createSkillApi(projectId: string, data: SkillCreateParams) {
  return request<ApiResponse<SkillDetail>>(`/projects/${projectId}/skills`, {
    method: "POST",
    body: data,
  });
}

export function updateSkillApi(skillId: string, data: SkillUpdateParams) {
  return request<ApiResponse<SkillDetail>>(`/skills/${skillId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteSkillApi(skillId: string) {
  return request<ApiResponse<null>>(`/skills/${skillId}`, { method: "DELETE" });
}

export function toggleSkillApi(skillId: string, isEnabled?: boolean) {
  return request<ApiResponse<SkillDetail>>(`/skills/${skillId}/toggle`, {
    method: "POST",
    body: isEnabled === undefined ? {} : { is_enabled: isEnabled },
  });
}

export function rescanSkillApi(skillId: string) {
  return request<ApiResponse<SkillDetail>>(`/skills/${skillId}/scan`, { method: "POST" });
}

export function importSkillZipApi(projectId: string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return request<ApiResponse<{ preview: SkillImportPreview }>>(
    `/projects/${projectId}/skills/import`,
    { method: "POST", body: fd },
  );
}

export function importSkillUrlApi(projectId: string, url: string, ref?: string) {
  return request<ApiResponse<SkillDetail>>(`/projects/${projectId}/skills/import-url`, {
    method: "POST",
    body: { url, ref: ref || undefined },
  });
}

export function matchTriggersApi(projectId: string, message: string, max = 10) {
  return request<ApiResponse<SkillTriggerMatch[]>>(
    `/projects/${projectId}/skills/match-triggers`,
    { method: "POST", body: { message, max } },
  );
}

export function getSkillUsageStatsApi(projectId: string, days = 7) {
  return request<ApiResponse<SkillUsageStats>>(
    `/projects/${projectId}/skills/usage-stats?days=${days}`,
  );
}

export function getSkillUsageTrendApi(
  projectId: string,
  days = 30,
  skillId?: string,
) {
  const sp = new URLSearchParams();
  sp.set("days", String(days));
  if (skillId) sp.set("skill_id", skillId);
  return request<ApiResponse<SkillUsageTrend>>(
    `/projects/${projectId}/skills/usage-trend?${sp.toString()}`,
  );
}

export function getSkillFailuresApi(skillId: string, limit = 20) {
  return request<ApiResponse<SkillUsageFailure[]>>(
    `/skills/${skillId}/failures?limit=${limit}`,
  );
}

export function listManualForChatApi(projectId: string) {
  return request<ApiResponse<SkillListItem[]>>(`/projects/${projectId}/skills/manual-for-chat`);
}

export function listActiveForChatApi(projectId: string) {
  return request<
    ApiResponse<{ always: SkillListItem[]; agent_callable: SkillListItem[] }>
  >(`/projects/${projectId}/skills/active-for-chat`);
}

export function activateManualSkillsApi(
  projectId: string,
  sessionId: string,
  manualSkillIds: string[],
) {
  return request<ApiResponse<{ manual_skill_ids: string[] }>>(
    `/projects/${projectId}/skills/chat/activate-manual`,
    { method: "POST", body: { session_id: sessionId, manual_skill_ids: manualSkillIds } },
  );
}

export function exportSkillUrl(skillId: string): string {
  return `/api/skills/${skillId}/export`;
}

/**
 * 直接下载 ZIP 流（带 Authorization 头）。浏览器原生 `<a download>` 无法注入 token，
 * 因此走 fetch 拿 Blob 再触发下载链接。
 */
export async function downloadSkillZip(skillId: string, slug: string): Promise<void> {
  const token = localStorage.getItem("access_token") || "";
  const resp = await fetch(exportSkillUrl(skillId), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) {
    throw new Error(`导出失败 (HTTP ${resp.status})`);
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug || "skill"}.zip`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─── Display helpers ─────────────────────────────────────────────────────────

export const SKILL_ACTIVATION_LABEL: Record<SkillActivationMode, string> = {
  manual: "手动",
  trigger: "触发词",
  agent_callable: "Agent 调用",
  always: "始终激活",
  auto_apply: "自动注入",
};

export const SKILL_SOURCE_LABEL: Record<SkillSource, string> = {
  built_in: "内置",
  imported: "导入",
  custom: "自定义",
};

export const SKILL_SAFETY_LABEL: Record<SkillSafetyStatus, string> = {
  unscanned: "未扫描",
  clean: "通过",
  warning: "警告",
  blocked: "拦截",
};

export const SKILL_SAFETY_BADGE_TYPE: Record<
  SkillSafetyStatus,
  "success" | "warning" | "default" | "error"
> = {
  unscanned: "default",
  clean: "success",
  warning: "warning",
  blocked: "error",
};

/** Task 12.6 — 激活原因到中文短标签的映射（banner / badge 共用）。 */
export const SKILL_ACTIVATION_REASON_LABEL: Record<SkillActivationReason, string> = {
  always: "始终激活",
  manual: "用户手动选中",
  trigger_match: "触发词命中",
  agent_callable: "Agent 主动调用",
  auto_apply: "自动注入",
};

/** 激活徽章颜色（与 reason 一一对应）。 */
export const SKILL_ACTIVATION_REASON_COLOR: Record<
  SkillActivationReason,
  "info" | "success" | "warning" | "error" | "default"
> = {
  always: "warning",
  manual: "info",
  trigger_match: "success",
  agent_callable: "info",
  auto_apply: "default",
};
