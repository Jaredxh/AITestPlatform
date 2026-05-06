<template>
  <div class="env-list-page">
    <page-header
      title="UI 自动化环境"
      subtitle="每个项目可以有多个执行目标（dev / staging / prod-readonly 等），各带独立的登录态和安全白名单"
      icon="i-carbon-cloud-services"
    >
      <template #extra>
        <n-button
          quaternary
          :disabled="!projectStore.currentProjectId"
          @click="goExecutionHistory"
        >
          <template #icon><span class="i-carbon-recording" /></template>
          查看执行历史
        </n-button>
        <n-button
          type="primary"
          :disabled="!projectStore.currentProjectId"
          @click="openCreate"
        >
          <template #icon><span class="i-carbon-add" /></template>
          新建环境
        </n-button>
      </template>
    </page-header>

    <n-alert v-if="!projectStore.currentProjectId" type="warning" class="mb-4">
      请先在顶栏选择一个项目，再管理 UI 自动化环境。
    </n-alert>

    <n-spin v-else :show="loading">
      <n-grid
        v-if="environments.length > 0"
        :cols="3"
        :x-gap="16"
        :y-gap="16"
        responsive="screen"
        :item-responsive="true"
      >
        <n-gi v-for="env in environments" :key="env.id" span="0:3 768:1">
          <div class="env-card card-hover" @click="handleEdit(env)">
            <div class="env-card__header">
              <div class="env-card__icon">
                <span class="i-carbon-cloud-services" />
              </div>
              <div class="env-card__title-block">
                <div class="env-card__title">
                  <n-ellipsis :tooltip="false">{{ env.name }}</n-ellipsis>
                </div>
                <span class="env-card__url">
                  <n-ellipsis :tooltip="false">{{ env.base_url }}</n-ellipsis>
                </span>
              </div>
            </div>

            <n-ellipsis v-if="env.description" :line-clamp="2" class="env-card__desc">
              {{ env.description }}
            </n-ellipsis>
            <p v-else class="env-card__desc env-card__desc--placeholder">
              未填写环境说明
            </p>

            <div class="env-card__tags">
              <n-tag
                :type="stateHealthTagType(env)"
                :bordered="false"
                size="small"
              >
                <template #icon>
                  <span :class="stateHealthIcon(env)" />
                </template>
                {{ stateHealthLabel(env) }}
              </n-tag>
              <n-tag
                v-if="env.enable_browser_evaluate"
                type="warning"
                :bordered="false"
                size="small"
              >
                <template #icon><span class="i-carbon-warning" /></template>
                允许执行 JS
              </n-tag>
              <n-tag
                v-if="!env.headless"
                type="info"
                :bordered="false"
                size="small"
              >
                <template #icon><span class="i-carbon-view" /></template>
                非 headless
              </n-tag>
            </div>

            <div class="env-card__meta">
              <div class="env-card__meta-item">
                <span class="i-carbon-flow" />
                <span>前置步骤 {{ env.preconditions_count ?? 0 }}</span>
              </div>
              <div class="env-card__meta-item">
                <span class="i-carbon-secure-profile" />
                <span>允许域名 {{ env.allowed_hosts.length }}</span>
              </div>
              <div class="env-card__meta-item">
                <span class="i-carbon-meter" />
                <span>{{ formatBudget(env.token_budget) }}</span>
              </div>
            </div>

            <div class="env-card__footer" @click.stop>
              <n-button size="small" quaternary @click="handleEdit(env)">
                <template #icon><span class="i-carbon-edit" /></template>
                编辑
              </n-button>
              <n-popconfirm
                :disabled="!env.state_saved_at"
                @positive-click="handleClearState(env)"
              >
                <template #trigger>
                  <n-button
                    size="small"
                    quaternary
                    :disabled="!env.state_saved_at"
                  >
                    <template #icon><span class="i-carbon-erase" /></template>
                    清空登录态
                  </n-button>
                </template>
                清空「{{ env.name }}」的已保存登录态？下次运行会重新登录。
              </n-popconfirm>
              <n-popconfirm @positive-click="handleDelete(env)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">
                    <template #icon><span class="i-carbon-trash-can" /></template>
                    删除
                  </n-button>
                </template>
                确认删除环境「{{ env.name }}」？前置步骤和已保存登录态会一并清除。
              </n-popconfirm>
            </div>
          </div>
        </n-gi>
      </n-grid>

      <app-empty
        v-else-if="!loading"
        icon="i-carbon-cloud-offline"
        title="还没有 UI 自动化环境"
        description="一个项目可以有多个环境（dev / staging / 只读 prod 等），每个环境带自己的登录态、域名白名单和前置步骤"
      >
        <template #actions>
          <n-button type="primary" @click="openCreate">
            <template #icon><span class="i-carbon-add" /></template>
            创建第一个环境
          </n-button>
        </template>
      </app-empty>
    </n-spin>

    <div v-if="total > pageSize" class="mt-4 flex justify-end">
      <n-pagination
        v-model:page="currentPage"
        :item-count="total"
        :page-size="pageSize"
        @update:page="handlePageChange"
      />
    </div>

    <environment-wizard
      v-model:show="wizardVisible"
      :project-id="projectStore.currentProjectId ?? ''"
      :environment-id="editingEnvId"
      @saved="handleSaved"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NEllipsis,
  NGi,
  NGrid,
  NPagination,
  NPopconfirm,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";

import {
  listEnvironmentsApi,
  deleteEnvironmentApi,
  clearEnvironmentStateApi,
  computeStateHealth,
} from "@/services/uiAutomation";
import type {
  TestEnvironment,
  StateHealth,
} from "@/services/uiAutomation";
import { useProjectStore } from "@/stores/project";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import EnvironmentWizard from "@/components/ui-automation/EnvironmentWizard.vue";

type EnvironmentWithPreCount = TestEnvironment & { preconditions_count?: number };

const projectStore = useProjectStore();
const message = useMessage();
const router = useRouter();

const loading = ref(false);
const environments = ref<EnvironmentWithPreCount[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = 24;

const wizardVisible = ref(false);
const editingEnvId = ref<string | null>(null);

function goExecutionHistory() {
  if (!projectStore.currentProjectId) return;
  router.push({
    name: "UIExecutionHistory",
    params: { projectId: projectStore.currentProjectId },
  });
}

async function fetchList() {
  if (!projectStore.currentProjectId) {
    environments.value = [];
    total.value = 0;
    return;
  }
  loading.value = true;
  try {
    const res = await listEnvironmentsApi(projectStore.currentProjectId, {
      page: currentPage.value,
      page_size: pageSize,
    });
    if (res.success) {
      // 后端列表接口不含 preconditions 详情；这里保留字段以便未来扩展
      environments.value = res.data.items as EnvironmentWithPreCount[];
      total.value = res.data.total;
    }
  } catch {
    message.error("获取环境列表失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingEnvId.value = null;
  wizardVisible.value = true;
}

function handleEdit(env: TestEnvironment) {
  editingEnvId.value = env.id;
  wizardVisible.value = true;
}

async function handleDelete(env: TestEnvironment) {
  try {
    const res = await deleteEnvironmentApi(env.id);
    if (res.success) {
      message.success("环境已删除");
      fetchList();
    }
  } catch {
    message.error("删除失败");
  }
}

async function handleClearState(env: TestEnvironment) {
  try {
    const res = await clearEnvironmentStateApi(env.id);
    if (res.success) {
      if (res.data.state_file_removed) {
        message.success("登录态已清除，下次运行会重新登录");
      } else {
        message.info("本来就没有已保存的登录态");
      }
      fetchList();
    }
  } catch {
    message.error("清除登录态失败");
  }
}

function handlePageChange(p: number) {
  currentPage.value = p;
  fetchList();
}

function handleSaved() {
  fetchList();
}

// ─── state 健康度 UI helpers ────────────────────────────────────────

function getHealth(env: TestEnvironment): StateHealth {
  return computeStateHealth(env.state_saved_at);
}

function stateHealthTagType(env: TestEnvironment): "success" | "warning" | "default" {
  const h = getHealth(env);
  if (h.kind === "fresh") return "success";
  if (h.kind === "stale") return "warning";
  return "default";
}

function stateHealthIcon(env: TestEnvironment): string {
  const h = getHealth(env);
  if (h.kind === "fresh") return "i-carbon-checkmark-filled";
  if (h.kind === "stale") return "i-carbon-time";
  return "i-carbon-dashed-circle";
}

function stateHealthLabel(env: TestEnvironment): string {
  return getHealth(env).label;
}

function formatBudget(budget: number): string {
  if (budget >= 10_000) return `${Math.round(budget / 1000)}K tokens`;
  return `${budget} tokens`;
}

// ─── 生命周期 ────────────────────────────────────────────────────────

onMounted(fetchList);

// 顶栏切换项目时自动刷新
watch(
  () => projectStore.currentProjectId,
  () => {
    currentPage.value = 1;
    fetchList();
  },
);
</script>

<style scoped>
.env-list-page {
  padding-bottom: 6rem;
  margin-bottom: 0.5rem;
}

.env-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 14px 18px 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 180px;
}

.env-card__header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.env-card__icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background: var(--brand-gradient-soft);
  color: var(--brand-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}

.env-card__title-block {
  flex: 1;
  min-width: 0;
}

.env-card__title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
}

.env-card__url {
  display: block;
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: var(--font-mono, ui-monospace, monospace);
  margin-top: 2px;
}

.env-card__desc {
  font-size: 13px;
  color: var(--text-tertiary);
  line-height: 1.5;
  margin: 0;
}

.env-card__desc--placeholder {
  color: var(--text-quaternary, #b1b5bc);
  font-style: italic;
}

.env-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.env-card__meta {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-tertiary);
  padding-top: 8px;
  border-top: 1px dashed var(--border-subtle);
  margin-top: auto;
}

.env-card__meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
}

.env-card__footer {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 0 2px;
  margin-top: 6px;
  border-top: 1px solid var(--border-subtle);
  background: var(--bg-card);
  position: relative;
  z-index: 1;
}

/* 卡片底部的操作按钮：用 secondary 风格让背景色更实，避免和卡片底色融在一起
   导致用户感觉"按钮被页面背景遮住"。*/
.env-card__footer :deep(.n-button) {
  background: var(--bg-page);
}

.env-card__footer :deep(.n-button:hover) {
  background: var(--bg-active);
}

</style>
