import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/login/LoginView.vue"),
    meta: { requiresAuth: false },
  },
  {
    path: "/",
    component: () => import("@/layouts/MainLayout.vue"),
    meta: { requiresAuth: true },
    redirect: "/dashboard",
    children: [
      {
        path: "dashboard",
        name: "Dashboard",
        component: () => import("@/views/dashboard/DashboardView.vue"),
      },
      {
        path: "projects",
        name: "ProjectList",
        component: () => import("@/views/projects/ProjectList.vue"),
      },
      {
        path: "projects/:projectId/settings",
        name: "ProjectSettings",
        component: () => import("@/views/projects/ProjectSettings.vue"),
      },
      {
        path: "chat",
        name: "AIChat",
        component: () => import("@/views/chat/ChatView.vue"),
        meta: { fluid: true },
      },
      {
        path: "testcases",
        name: "TestcaseList",
        component: () => import("@/views/testcases/TestcaseView.vue"),
        meta: { permission: "testcase:view", keepAlive: true },
      },
      {
        path: "requirements",
        name: "RequirementList",
        component: () => import("@/views/requirements/RequirementList.vue"),
        meta: { permission: "requirement:view" },
      },
      {
        path: "requirements/:documentId",
        name: "RequirementDetail",
        component: () => import("@/views/requirements/RequirementDetail.vue"),
        meta: { permission: "requirement:view" },
      },
      {
        path: "ui-environments",
        name: "UIEnvironmentList",
        component: () => import("@/views/ui-automation/EnvironmentList.vue"),
        meta: { permission: "ui_env:view" },
      },
      {
        path: "projects/:projectId/ui-environments",
        name: "UIEnvironmentListForProject",
        component: () => import("@/views/ui-automation/EnvironmentList.vue"),
        meta: { permission: "ui_env:view" },
      },
      {
        // Task 10.2：实时 SSE 执行监控页（支持 ?replay=1 走重放端点）
        path: "projects/:projectId/ui-executions/:execId/monitor",
        name: "UIExecutionMonitor",
        component: () => import("@/views/ui-automation/ExecutionMonitor.vue"),
        meta: { permission: "ui_exec:view" },
      },
      {
        // Task 10.3：执行历史列表（项目维度）
        path: "projects/:projectId/ui-executions",
        name: "UIExecutionHistory",
        component: () => import("@/views/ui-automation/ExecutionHistory.vue"),
        meta: { permission: "ui_exec:view" },
      },
      {
        // Task 10.3：执行详情主页（含物料快照 + 双视图通过率 + 用例折叠列表）
        path: "projects/:projectId/ui-executions/:execId/detail",
        name: "UIExecutionDetail",
        component: () => import("@/views/ui-automation/ExecutionDetail.vue"),
        meta: { permission: "ui_exec:view" },
      },
      {
        // 全局入口：根据 currentProjectId 跳到该项目下的执行历史；侧栏菜单用
        path: "ui-executions",
        name: "UIExecutionHistoryGlobal",
        redirect: () => {
          const pid = localStorage.getItem("current_project_id");
          return pid
            ? { name: "UIExecutionHistory", params: { projectId: pid } }
            : { name: "Dashboard" };
        },
        meta: { permission: "ui_exec:view" },
      },
      {
        path: "projects/:projectId/test-data",
        name: "TestDataView",
        component: () => import("@/views/test-data/TestDataView.vue"),
        meta: { permission: "test_data:view" },
      },
      {
        path: "projects/:projectId/test-data/sets/:setId",
        name: "TestDataSetEditor",
        component: () => import("@/views/test-data/DataSetEditor.vue"),
        meta: { permission: "test_data:view" },
      },
      {
        path: "test-data",
        name: "TestDataViewGlobal",
        redirect: () => {
          const pid = localStorage.getItem("current_project_id");
          return pid
            ? { name: "TestDataView", params: { projectId: pid } }
            : { name: "Dashboard" };
        },
        meta: { permission: "test_data:view" },
      },
      {
        path: "settings/llm",
        name: "LLMConfig",
        component: () => import("@/views/settings/LLMConfigView.vue"),
        meta: { permission: "llm:config" },
      },
      {
        path: "settings/prompts",
        name: "PromptManagement",
        component: () => import("@/views/settings/PromptManagement.vue"),
        meta: { permission: "requirement:view" },
      },
      {
        path: "settings/users",
        name: "UserManagement",
        component: () => import("@/views/settings/UserManagement.vue"),
        meta: { permission: "user:manage" },
      },
      {
        path: "settings/roles",
        name: "RoleManagement",
        component: () => import("@/views/settings/RoleManagement.vue"),
        meta: { permission: "user:manage" },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const token = localStorage.getItem("access_token");

  if (to.meta.requiresAuth !== false && !token) {
    return { name: "Login", query: { redirect: to.fullPath } };
  }

  if (to.name === "Login" && token) {
    return { path: "/" };
  }

  if (token) {
    const { useAuthStore } = await import("@/stores/auth");
    const authStore = useAuthStore();
    if (!authStore.user) {
      await authStore.fetchUser();
    }

    const requiredPerm = to.meta.permission as string | undefined;
    if (requiredPerm && !authStore.hasPermission(requiredPerm)) {
      return { name: "Dashboard" };
    }
  }
});

export default router;
