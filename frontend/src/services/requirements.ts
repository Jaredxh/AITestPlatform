import { request } from "./request";
import type { ApiResponse } from "./auth";

export interface DocumentInfo {
  id: string;
  project_id: string;
  filename: string;
  file_size: number;
  content_type: string;
  status: string;
  uploaded_by: string;
  uploader_name: string | null;
  created_at: string;
  updated_at: string;
  review_status: "unreviewed" | "reviewing" | "reviewed" | "failed";
  review_count: number;
  last_review_score: number | null;
}

export interface DocumentDetail extends DocumentInfo {
  content_text: string | null;
  content_preview: string | null;
  /** 解析失败时由后端返回，前端据此提示用户。 */
  parse_error?: string | null;
}

export interface ReviewDimensionScore {
  score: number;
  comment: string;
}

export interface ReviewIssue {
  severity: "high" | "medium" | "low";
  category: string;
  description: string;
  location: string | null;
  suggestion: string | null;
}

export interface ReviewInfo {
  id: string;
  document_id: string;
  reviewer_id: string;
  reviewer_name: string | null;
  llm_config_id: string | null;
  llm_config_name: string | null;
  model_used: string | null;
  status: string;
  overall_score: number | null;
  dimensions: Record<string, ReviewDimensionScore> | null;
  issues: ReviewIssue[] | null;
  summary: string | null;
  review_time_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewListItem {
  id: string;
  document_id: string;
  reviewer_name: string | null;
  model_used: string | null;
  status: string;
  overall_score: number | null;
  summary: string | null;
  review_time_ms: number | null;
  created_at: string;
}

export interface PaginatedDocuments {
  items: DocumentInfo[];
  total: number;
  page: number;
  page_size: number;
}

export function uploadDocumentApi(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return request<ApiResponse<DocumentDetail>>(
    `/requirements/projects/${projectId}/documents`,
    { method: "POST", body: formData },
  );
}

export function getDocumentsApi(
  projectId: string,
  params?: { page?: number; page_size?: number; search?: string },
) {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  if (params?.search) qs.set("search", params.search);
  const query = qs.toString() ? `?${qs.toString()}` : "";
  return request<ApiResponse<PaginatedDocuments>>(
    `/requirements/projects/${projectId}/documents${query}`,
  );
}

export function getDocumentDetailApi(documentId: string) {
  return request<ApiResponse<DocumentDetail>>(
    `/requirements/documents/${documentId}`,
  );
}

export function deleteDocumentApi(documentId: string) {
  return request<ApiResponse<null>>(`/requirements/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function updateDocumentApi(
  documentId: string,
  data: { filename?: string; content_text?: string },
) {
  return request<ApiResponse<DocumentDetail>>(
    `/requirements/documents/${documentId}`,
    { method: "PUT", body: data },
  );
}

export function deleteReviewApi(reviewId: string) {
  return request<ApiResponse<null>>(`/requirements/reviews/${reviewId}`, {
    method: "DELETE",
  });
}

export function triggerReviewApi(
  documentId: string,
  llmConfigId?: string,
) {
  return request<ApiResponse<ReviewInfo>>(
    `/requirements/documents/${documentId}/review`,
    {
      method: "POST",
      body: llmConfigId ? { llm_config_id: llmConfigId } : undefined,
    },
  );
}

export function getDocumentReviewsApi(documentId: string) {
  return request<ApiResponse<ReviewListItem[]>>(
    `/requirements/documents/${documentId}/reviews`,
  );
}

export function getReviewDetailApi(reviewId: string) {
  return request<ApiResponse<ReviewInfo>>(`/requirements/reviews/${reviewId}`);
}
