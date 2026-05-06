import { request } from "./request";
import type { ApiResponse } from "./auth";

export interface ProjectMember {
  user_id: string;
  username: string;
  display_name: string | null;
  role: string;
  joined_at: string;
}

export interface ProjectInfo {
  id: string;
  name: string;
  description: string | null;
  status: string;
  owner_id: string;
  owner_name: string | null;
  member_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends ProjectInfo {
  members: ProjectMember[];
}

export interface PaginatedProjects {
  items: ProjectInfo[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectCreateParams {
  name: string;
  description?: string;
}

export interface ProjectUpdateParams {
  name?: string;
  description?: string;
  status?: "active" | "archived";
}

export interface MemberAddParams {
  user_id: string;
  role?: "admin" | "member" | "viewer";
}

export function getProjectsApi(params: { page?: number; page_size?: number; search?: string } = {}) {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.search) query.set("search", params.search);
  const qs = query.toString();
  return request<ApiResponse<PaginatedProjects>>(`/projects${qs ? `?${qs}` : ""}`);
}

export function getProjectDetailApi(projectId: string) {
  return request<ApiResponse<ProjectDetail>>(`/projects/${projectId}`);
}

export function createProjectApi(data: ProjectCreateParams) {
  return request<ApiResponse<ProjectDetail>>("/projects", {
    method: "POST",
    body: data,
  });
}

export function updateProjectApi(projectId: string, data: ProjectUpdateParams) {
  return request<ApiResponse<ProjectInfo>>(`/projects/${projectId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteProjectApi(projectId: string) {
  return request<ApiResponse<null>>(`/projects/${projectId}`, { method: "DELETE" });
}

export function addProjectMemberApi(projectId: string, data: MemberAddParams) {
  return request<ApiResponse<ProjectMember>>(`/projects/${projectId}/members`, {
    method: "POST",
    body: data,
  });
}

export function removeProjectMemberApi(projectId: string, userId: string) {
  return request<ApiResponse<null>>(`/projects/${projectId}/members/${userId}`, {
    method: "DELETE",
  });
}
