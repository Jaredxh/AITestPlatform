import { ofetch } from "ofetch";

let isRefreshing = false;
let pendingRequests: Array<() => void> = [];

function getAccessToken() {
  return localStorage.getItem("access_token") || "";
}

function getRefreshToken() {
  return localStorage.getItem("refresh_token") || "";
}

function clearTokens() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

function redirectToLogin() {
  clearTokens();
  if (window.location.pathname !== "/login") {
    window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`;
  }
}

async function doRefresh(): Promise<boolean> {
  const rt = getRefreshToken();
  if (!rt) return false;
  try {
    const res = await ofetch<{
      success: boolean;
      data: { access_token: string; refresh_token: string };
    }>("/api/auth/refresh", {
      method: "POST",
      body: { refresh_token: rt },
    });
    if (res.success) {
      localStorage.setItem("access_token", res.data.access_token);
      localStorage.setItem("refresh_token", res.data.refresh_token);
      return true;
    }
  } catch {
    /* refresh failed */
  }
  return false;
}

export const request = ofetch.create({
  baseURL: "/api",

  onRequest({ options }) {
    const token = getAccessToken();
    if (token) {
      const headers = new Headers(options.headers as HeadersInit);
      headers.set("Authorization", `Bearer ${token}`);
      options.headers = headers;
    }
  },

  async onResponseError({ response, options }) {
    if (response.status !== 401) return;

    const retryWithToken = () => {
      const headers = new Headers(options.headers as HeadersInit);
      headers.set("Authorization", `Bearer ${getAccessToken()}`);
      return ofetch(response.url, { ...options, headers });
    };

    if (isRefreshing) {
      await new Promise<void>((resolve) => {
        pendingRequests.push(() => resolve());
      });
      retryWithToken();
      return;
    }

    isRefreshing = true;
    const ok = await doRefresh();
    isRefreshing = false;

    if (ok) {
      pendingRequests.forEach((cb) => cb());
      pendingRequests = [];
      retryWithToken();
      return;
    }

    pendingRequests = [];
    redirectToLogin();
  },
});
