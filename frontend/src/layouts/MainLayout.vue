<template>
  <n-layout has-sider class="main-layout">
    <n-layout-sider
      bordered
      :collapsed="collapsed"
      collapse-mode="width"
      :collapsed-width="72"
      :width="232"
      show-trigger="bar"
      :native-scrollbar="false"
      class="main-layout__sider"
      @collapse="collapsed = true"
      @expand="collapsed = false"
    >
      <app-logo :collapsed="collapsed" />
      <n-menu
        :collapsed="collapsed"
        :collapsed-width="72"
        :collapsed-icon-size="20"
        :options="filteredMenuOptions"
        :value="currentRoute"
        :indent="22"
        class="main-layout__menu"
        @update:value="handleMenuClick"
      />
    </n-layout-sider>

    <n-layout class="main-layout__inner">
      <n-layout-header bordered class="main-layout__header app-glass">
        <div class="main-layout__header-left">
          <project-selector />
          <div class="app-divider" />
          <app-breadcrumb :items="breadcrumbs" />
        </div>
        <div class="main-layout__header-right">
          <n-tooltip placement="bottom">
            <template #trigger>
              <n-button quaternary circle size="small" @click="themeStore.toggle">
                <template #icon>
                  <span :class="themeStore.isDark ? 'i-carbon-sun' : 'i-carbon-moon'" />
                </template>
              </n-button>
            </template>
            {{ themeStore.isDark ? "切换到浅色模式" : "切换到深色模式" }}
          </n-tooltip>
          <n-dropdown :options="userMenuOptions" trigger="click" @select="handleUserMenu">
            <button class="user-chip" type="button">
              <n-avatar :size="28" round class="user-chip__avatar">
                {{ avatarText }}
              </n-avatar>
              <span class="user-chip__name">{{ displayName }}</span>
              <span class="i-carbon-chevron-down user-chip__caret" />
            </button>
          </n-dropdown>
        </div>
      </n-layout-header>

      <n-layout-content
        v-if="!route.meta.fluid"
        class="app-content"
        :native-scrollbar="false"
      >
        <router-view v-slot="{ Component, route: r }">
          <transition name="fade-page" mode="out-in">
            <keep-alive :include="['TestcaseView']">
              <component :is="Component" :key="r.meta.keepAlive ? r.name : r.fullPath" />
            </keep-alive>
          </transition>
        </router-view>
      </n-layout-content>
      <div v-else class="app-content--fluid">
        <router-view v-slot="{ Component, route: r }">
          <transition name="fade-page" mode="out-in">
            <keep-alive :include="['TestcaseView']">
              <component :is="Component" :key="r.meta.keepAlive ? r.name : r.fullPath" />
            </keep-alive>
          </transition>
        </router-view>
      </div>
    </n-layout>
  </n-layout>
</template>

<script setup lang="ts">
import { ref, computed, h, onMounted } from "vue";
import { useRouter, useRoute } from "vue-router";
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  NButton,
  NDropdown,
  NAvatar,
  NTooltip,
  useDialog,
} from "naive-ui";
import type { MenuOption } from "naive-ui";
import { useThemeStore } from "@/stores/theme";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import { usePermission } from "@/composables/usePermission";
import ProjectSelector from "@/components/common/ProjectSelector.vue";
import AppLogo from "@/components/common/AppLogo.vue";
import AppBreadcrumb from "@/components/common/AppBreadcrumb.vue";
import type { BreadcrumbItem } from "@/components/common/AppBreadcrumb.vue";

const router = useRouter();
const route = useRoute();
const themeStore = useThemeStore();
const authStore = useAuthStore();
const projectStore = useProjectStore();
const { isAdmin } = usePermission();
const dialog = useDialog();
const collapsed = ref(false);

// 某些路由使用动态 params（/projects/:id/test-data/...），但菜单里用的是
// 一个通用的"全局入口" key；这里做一层映射，保证 children 路由也能高亮菜单。
const routeToMenuKey: Record<string, string> = {
  TestDataView: "TestDataViewGlobal",
  TestDataSetEditor: "TestDataViewGlobal",
  UIEnvironmentListForProject: "UIEnvironmentList",
  // 执行历史/详情/监控统一高亮「执行历史」子菜单（Task 10.3）
  UIExecutionHistory: "UIExecutionHistoryGlobal",
  UIExecutionDetail: "UIExecutionHistoryGlobal",
  UIExecutionMonitor: "UIExecutionHistoryGlobal",
  ProjectSettings: "ProjectList",
  RequirementDetail: "RequirementList",
};

const currentRoute = computed(() => {
  const name = route.name as string;
  return routeToMenuKey[name] ?? name;
});

type AppMenuOption = MenuOption & { visible?: () => boolean; children?: MenuOption[] };

// 主菜单顺序（2026-05 用户验收反馈调整）：
// 概览 → 项目管理 → 需求管理 → 用例管理 → 测试物料 → UI 自动化 → AI 对话 → 系统设置
// 设计依据：按"测试工作流"从上到下排——先建项目，再上传需求出用例，
// 配物料后跑 UI 自动化；AI 对话作为辅助工具靠后；系统设置永远兜底。
const allMenuOptions: AppMenuOption[] = [
  {
    label: "概览",
    key: "Dashboard",
    icon: () => h("span", { class: "i-carbon-dashboard" }),
  },
  {
    label: "项目管理",
    key: "ProjectList",
    icon: () => h("span", { class: "i-carbon-folder" }),
  },
  {
    label: "需求管理",
    key: "RequirementList",
    icon: () => h("span", { class: "i-carbon-document" }),
  },
  {
    label: "用例管理",
    key: "TestcaseList",
    icon: () => h("span", { class: "i-carbon-task" }),
  },
  {
    label: "测试物料",
    key: "TestDataViewGlobal",
    icon: () => h("span", { class: "i-carbon-data-categorical" }),
  },
  {
    label: "UI 自动化",
    key: "ui-automation",
    icon: () => h("span", { class: "i-carbon-cloud-services" }),
    children: [
      { label: "环境管理", key: "UIEnvironmentList" },
      { label: "执行历史", key: "UIExecutionHistoryGlobal" },
    ],
  },
  {
    label: "AI 对话",
    key: "AIChat",
    icon: () => h("span", { class: "i-carbon-chat-bot" }),
  },
  {
    label: "系统设置",
    key: "settings",
    icon: () => h("span", { class: "i-carbon-settings" }),
    visible: () => isAdmin.value,
    children: [
      { label: "LLM 配置", key: "LLMConfig" },
      { label: "提示词管理", key: "PromptManagement" },
      { label: "用户管理", key: "UserManagement" },
      { label: "角色管理", key: "RoleManagement" },
    ],
  },
];

const filteredMenuOptions = computed<MenuOption[]>(() =>
  allMenuOptions.filter((item) => !item.visible || item.visible()),
);

const routeLabelMap: Record<string, string> = {
  Dashboard: "概览",
  AIChat: "AI 对话",
  RequirementList: "需求管理",
  RequirementDetail: "文档详情",
  TestcaseList: "测试用例",
  UIEnvironmentList: "UI 环境",
  UIEnvironmentListForProject: "UI 环境",
  UIExecutionHistory: "执行历史",
  UIExecutionHistoryGlobal: "执行历史",
  UIExecutionDetail: "执行详情",
  UIExecutionMonitor: "执行监控",
  TestDataView: "测试物料",
  TestDataViewGlobal: "测试物料",
  TestDataSetEditor: "物料集",
  ProjectList: "项目管理",
  ProjectSettings: "项目设置",
  LLMConfig: "LLM 配置",
  PromptManagement: "提示词管理",
  UserManagement: "用户管理",
  RoleManagement: "角色管理",
};

const settingsRoutes = new Set([
  "LLMConfig",
  "PromptManagement",
  "UserManagement",
  "RoleManagement",
]);

const breadcrumbs = computed<BreadcrumbItem[]>(() => {
  const name = route.name as string;
  if (!name) return [];
  if (name === "Dashboard") {
    return [{ label: "概览" }];
  }
  const items: BreadcrumbItem[] = [{ label: "首页", to: { name: "Dashboard" } }];

  if (settingsRoutes.has(name)) {
    items.push({ label: "系统设置" });
  } else if (name === "RequirementDetail") {
    items.push({ label: "需求管理", to: { name: "RequirementList" } });
  } else if (name === "ProjectSettings") {
    items.push({ label: "项目管理", to: { name: "ProjectList" } });
  } else if (name === "TestDataSetEditor") {
    const pid = route.params.projectId as string | undefined;
    items.push({
      label: "测试物料",
      to: pid
        ? { name: "TestDataView", params: { projectId: pid } }
        : { name: "TestDataViewGlobal" },
    });
  } else if (
    name === "UIExecutionHistory" ||
    name === "UIExecutionDetail" ||
    name === "UIExecutionMonitor"
  ) {
    items.push({ label: "UI 自动化", to: { name: "UIEnvironmentList" } });
    if (name !== "UIExecutionHistory") {
      const pid = route.params.projectId as string | undefined;
      items.push({
        label: "执行历史",
        to: pid ? { name: "UIExecutionHistory", params: { projectId: pid } } : undefined,
      });
    }
  }

  items.push({ label: routeLabelMap[name] || "" });
  return items;
});

const displayName = computed(
  () => authStore.user?.display_name || authStore.user?.username || "用户",
);
const avatarText = computed(() => displayName.value.charAt(0).toUpperCase());

const userMenuOptions = [
  { label: "个人信息", key: "profile" },
  { type: "divider", key: "d1" },
  { label: "退出登录", key: "logout" },
];

function handleMenuClick(key: string) {
  router.push({ name: key });
}

function handleUserMenu(key: string) {
  if (key === "logout") {
    dialog.warning({
      title: "确认退出",
      content: "确定要退出登录吗？",
      positiveText: "退出",
      negativeText: "取消",
      onPositiveClick: () => authStore.logout(),
    });
  }
}

onMounted(() => {
  projectStore.fetchProjects();
});
</script>

<style scoped>
.main-layout {
  height: 100vh;
}

.main-layout__sider {
  background: var(--bg-sider) !important;
  box-shadow: 1px 0 8px rgba(15, 23, 42, 0.04), 0 0 0 1px var(--border-subtle) inset;
  position: relative;
  z-index: 2;
}

.main-layout__sider :deep(.n-layout-sider-scroll-container) {
  display: flex;
  flex-direction: column;
}

.main-layout__menu {
  padding: 8px 10px;
  flex: 1;
}

.main-layout__menu :deep(.n-menu-item) {
  margin-bottom: 2px;
}

.main-layout__menu :deep(.n-menu-item-content) {
  border-radius: var(--radius-md);
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    color var(--duration-fast) var(--easing-standard);
}

.main-layout__menu :deep(.n-menu-item-content:not(.n-menu-item-content--disabled):hover) {
  background-color: var(--bg-active);
}

.main-layout__menu :deep(.n-menu-item-content.n-menu-item-content--selected) {
  background: var(--brand-primary-soft);
  box-shadow: inset 3px 0 0 var(--brand-primary);
  font-weight: 600;
}

.main-layout__menu :deep(.n-submenu .n-menu-item-content) {
  font-size: 13px;
}

.main-layout__inner {
  background-color: var(--bg-page);
}

.main-layout__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--layout-header-height);
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.main-layout__header-left,
.main-layout__header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 4px 4px;
  border-radius: var(--radius-pill);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    border-color var(--duration-fast) var(--easing-standard);
  color: var(--text-primary);
  font: inherit;
}

.user-chip:hover {
  background-color: var(--bg-active);
  border-color: var(--brand-primary-border);
}

.user-chip__avatar {
  background: var(--brand-gradient) !important;
  color: #fff !important;
  font-weight: 600;
}

.user-chip__name {
  font-size: 13px;
  font-weight: 500;
}

.user-chip__caret {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
