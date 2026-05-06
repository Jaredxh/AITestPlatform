import { request } from "./request";
import type { ApiResponse, UserInfo, RoleInfo } from "./auth";

export interface PaginatedUsers {
  items: UserInfo[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserCreateParams {
  username: string;
  email: string;
  password: string;
  display_name?: string;
  is_active?: boolean;
  role_ids?: string[];
}

export interface UserUpdateParams {
  display_name?: string;
  email?: string;
  is_active?: boolean;
  password?: string;
}

export function getUsersApi(params: { page?: number; page_size?: number; search?: string } = {}) {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.search) query.set("search", params.search);
  const qs = query.toString();
  return request<ApiResponse<PaginatedUsers>>(`/users${qs ? `?${qs}` : ""}`);
}

export function getUserApi(userId: string) {
  return request<ApiResponse<UserInfo>>(`/users/${userId}`);
}

export function createUserApi(data: UserCreateParams) {
  return request<ApiResponse<UserInfo>>("/users", {
    method: "POST",
    body: data,
  });
}

export function updateUserApi(userId: string, data: UserUpdateParams) {
  return request<ApiResponse<UserInfo>>(`/users/${userId}`, {
    method: "PATCH",
    body: data,
  });
}

export function updateUserRolesApi(userId: string, roleIds: string[]) {
  return request<ApiResponse<UserInfo>>(`/users/${userId}/roles`, {
    method: "PUT",
    body: { role_ids: roleIds },
  });
}

export function deleteUserApi(userId: string) {
  return request<ApiResponse<null>>(`/users/${userId}`, { method: "DELETE" });
}

export function getRolesApi() {
  return request<ApiResponse<RoleInfo[]>>("/users/roles");
}

export interface RoleCreateParams {
  name: string;
  display_name: string;
  description?: string;
  permissions?: string[];
}

export interface RoleUpdateParams {
  display_name?: string;
  description?: string;
  permissions?: string[];
}

export function createRoleApi(data: RoleCreateParams) {
  return request<ApiResponse<RoleInfo>>("/users/roles", {
    method: "POST",
    body: data,
  });
}

export function updateRoleApi(roleId: string, data: RoleUpdateParams) {
  return request<ApiResponse<RoleInfo>>(`/users/roles/${roleId}`, {
    method: "PATCH",
    body: data,
  });
}

export function deleteRoleApi(roleId: string) {
  return request<ApiResponse<null>>(`/users/roles/${roleId}`, {
    method: "DELETE",
  });
}
