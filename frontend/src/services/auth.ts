import { request } from "./request";

export interface LoginParams {
  username: string;
  password: string;
}

export interface RegisterParams {
  username: string;
  email: string;
  password: string;
  display_name?: string;
}

export interface UserInfo {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  is_superuser: boolean;
  roles: RoleInfo[];
  created_at: string;
  updated_at: string;
}

export interface RoleInfo {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  permissions: string[];
  is_system: boolean;
  created_at?: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface ApiResponse<T = unknown> {
  success: boolean;
  data: T;
  message: string | null;
  code: string | null;
}

export function loginApi(params: LoginParams) {
  return request<ApiResponse<{ user: UserInfo; tokens: TokenPair }>>(
    "/auth/login",
    { method: "POST", body: params },
  );
}

export function registerApi(params: RegisterParams) {
  return request<ApiResponse<UserInfo>>("/auth/register", {
    method: "POST",
    body: params,
  });
}

export function getMeApi() {
  return request<ApiResponse<UserInfo>>("/auth/me");
}

export function refreshTokenApi(refresh_token: string) {
  return request<ApiResponse<TokenPair>>("/auth/refresh", {
    method: "POST",
    body: { refresh_token },
  });
}
