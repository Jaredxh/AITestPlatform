import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { getMeApi, loginApi, registerApi, refreshTokenApi } from "@/services/auth";
import type { UserInfo, LoginParams, RegisterParams } from "@/services/auth";
import router from "@/router";

export const useAuthStore = defineStore("auth", () => {
  const user = ref<UserInfo | null>(null);
  const accessToken = ref(localStorage.getItem("access_token") || "");
  const refreshToken = ref(localStorage.getItem("refresh_token") || "");

  const isLoggedIn = computed(() => !!accessToken.value);
  const permissions = computed<Set<string>>(() => {
    if (!user.value) return new Set();
    if (user.value.is_superuser) return new Set(["*"]);
    const perms = new Set<string>();
    for (const role of user.value.roles) {
      for (const p of role.permissions) perms.add(p);
    }
    return perms;
  });

  function setTokens(access: string, refresh: string) {
    accessToken.value = access;
    refreshToken.value = refresh;
    localStorage.setItem("access_token", access);
    localStorage.setItem("refresh_token", refresh);
  }

  function clearAuth() {
    user.value = null;
    accessToken.value = "";
    refreshToken.value = "";
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
  }

  async function login(params: LoginParams) {
    const res = await loginApi(params);
    if (!res.success) throw new Error(res.message || "登录失败");
    setTokens(res.data.tokens.access_token, res.data.tokens.refresh_token);
    user.value = res.data.user;
    return res.data.user;
  }

  async function register(params: RegisterParams) {
    const res = await registerApi(params);
    if (!res.success) throw new Error(res.message || "注册失败");
    return res.data;
  }

  async function fetchUser() {
    if (!accessToken.value) return null;
    try {
      const res = await getMeApi();
      if (res.success) {
        user.value = res.data;
        return res.data;
      }
    } catch {
      clearAuth();
    }
    return null;
  }

  async function tryRefreshToken(): Promise<boolean> {
    if (!refreshToken.value) return false;
    try {
      const res = await refreshTokenApi(refreshToken.value);
      if (res.success) {
        setTokens(res.data.access_token, res.data.refresh_token);
        return true;
      }
    } catch {
      /* refresh failed */
    }
    clearAuth();
    return false;
  }

  function logout() {
    clearAuth();
    router.push({ name: "Login" });
  }

  function hasPermission(perm: string): boolean {
    if (!user.value) return false;
    if (user.value.is_superuser) return true;
    return permissions.value.has(perm);
  }

  return {
    user,
    accessToken,
    refreshToken,
    isLoggedIn,
    permissions,
    login,
    register,
    fetchUser,
    tryRefreshToken,
    logout,
    clearAuth,
    hasPermission,
  };
});
