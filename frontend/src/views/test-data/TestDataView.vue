<template>
  <div>
    <page-header
      title="测试物料"
      subtitle="为 AI 执行提供可复用的测试数据：账号池、订单参数、测试文件、参数化数据组等"
      icon="i-carbon-data-categorical"
    >
      <template #extra>
        <n-button
          type="primary"
          :disabled="!projectId || !canEdit"
          @click="openCreateDialog"
        >
          <template #icon><span class="i-carbon-add" /></template>
          新建物料集
        </n-button>
      </template>
    </page-header>

    <n-alert v-if="!projectId" type="warning" class="mb-4">
      请先在顶栏选择一个项目，再管理物料。
    </n-alert>

    <div v-else class="td-layout">
      <!-- 左侧 scope tab -->
      <aside class="td-sidebar">
        <n-scrollbar>
          <div class="td-tabs">
            <div
              v-for="tab in scopeTabs"
              :key="tab.key"
              class="td-tab"
              :class="{ 'td-tab--active': activeScope === tab.key }"
              @click="activeScope = tab.key"
            >
              <span :class="tab.icon" class="td-tab__icon" />
              <div class="td-tab__body">
                <div class="td-tab__title">{{ tab.label }}</div>
                <div class="td-tab__desc">{{ tab.description }}</div>
              </div>
              <span class="td-tab__badge">{{ countByScope[tab.key] ?? 0 }}</span>
            </div>
          </div>
        </n-scrollbar>
      </aside>

      <!-- 右侧卡片网格 -->
      <main class="td-main">
        <div class="td-main__toolbar">
          <n-input
            v-model:value="searchText"
            clearable
            placeholder="搜索物料集名称、描述"
            :style="{ width: '260px' }"
          >
            <template #prefix><span class="i-carbon-search" /></template>
          </n-input>
          <n-button size="small" quaternary :loading="loading" @click="fetchList">
            <template #icon><span class="i-carbon-renew" /></template>
            刷新
          </n-button>
        </div>

        <n-spin :show="loading">
          <n-grid
            v-if="filteredSets.length > 0"
            :cols="3"
            :x-gap="16"
            :y-gap="16"
            responsive="screen"
            :item-responsive="true"
          >
            <n-gi
              v-for="set in filteredSets"
              :key="set.id"
              span="0:3 768:2 1200:1"
            >
              <div class="td-card card-hover" @click="openEditor(set)">
                <div class="td-card__header">
                  <div class="td-card__icon">
                    <span :class="SCOPE_META[set.scope].icon" />
                  </div>
                  <div class="td-card__title-block">
                    <div class="td-card__title">
                      <n-ellipsis :tooltip="false">{{ set.name }}</n-ellipsis>
                    </div>
                    <div class="td-card__tags">
                      <n-tag
                        v-if="set.is_default"
                        size="tiny"
                        type="success"
                        :bordered="false"
                      >
                        <template #icon><span class="i-carbon-star-filled" /></template>
                        默认
                      </n-tag>
                      <n-tag
                        v-if="set.category"
                        size="tiny"
                        :bordered="false"
                      >
                        {{ displayCategoryLabel(set.category) }}
                      </n-tag>
                      <n-tag size="tiny" :bordered="false">
                        {{ set.item_count }} 项
                      </n-tag>
                    </div>
                  </div>
                </div>

                <n-ellipsis
                  v-if="set.description"
                  :line-clamp="2"
                  class="td-card__desc"
                >
                  {{ set.description }}
                </n-ellipsis>
                <p v-else class="td-card__desc td-card__desc--placeholder">
                  暂无描述
                </p>

                <div class="td-card__footer">
                  <span class="td-card__updated">
                    更新于 {{ formatRelative(set.updated_at) }}
                  </span>
                  <n-popconfirm
                    v-if="canEdit"
                    @positive-click.stop="handleDelete(set)"
                  >
                    <template #trigger>
                      <n-button
                        size="tiny"
                        quaternary
                        type="error"
                        @click.stop
                      >
                        <template #icon>
                          <span class="i-carbon-trash-can" />
                        </template>
                      </n-button>
                    </template>
                    确认删除「{{ set.name }}」？其下所有物料与关联文件会一并删除。
                  </n-popconfirm>
                </div>
              </div>
            </n-gi>
          </n-grid>

          <app-empty
            v-else-if="!loading && !searchText"
            :icon="scopeEmptyIcon"
            :title="scopeEmptyTitle"
            :description="scopeEmptyDesc"
          >
            <template #actions>
              <n-button
                type="primary"
                :disabled="!canEdit"
                @click="openCreateDialog"
              >
                <template #icon><span class="i-carbon-add" /></template>
                创建{{ scopeEmptyLabel }}
              </n-button>
            </template>
          </app-empty>

          <app-empty
            v-else-if="!loading && searchText"
            icon="i-carbon-search-locate"
            title="没有匹配的物料集"
            :description="`关键字「${searchText}」没命中任何名称或描述`"
          />
        </n-spin>

        <div v-if="total > pageSize" class="mt-4 flex justify-end">
          <n-pagination
            v-model:page="currentPage"
            :item-count="total"
            :page-size="pageSize"
            @update:page="handlePageChange"
          />
        </div>
      </main>
    </div>

    <!-- 新建物料集弹窗 -->
    <n-modal
      v-model:show="createVisible"
      preset="card"
      :style="{ width: '540px' }"
      title="新建物料集"
      :mask-closable="false"
    >
      <n-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-placement="left"
        label-width="80"
      >
        <n-form-item label="名称" path="name">
          <n-input
            v-model:value="createForm.name"
            placeholder="如：登录账号池 / 订单下单测试数据"
          />
        </n-form-item>
        <n-form-item label="可见范围" path="scope">
          <n-radio-group v-model:value="createForm.scope">
            <n-radio-button
              v-for="tab in scopeTabs"
              :key="tab.key"
              :value="tab.key"
            >
              <span :class="tab.icon" style="margin-right: 4px;" />
              {{ tab.label }}
            </n-radio-button>
          </n-radio-group>
        </n-form-item>
        <n-form-item
          v-if="createForm.scope === 'project'"
          label="项目默认"
        >
          <n-switch v-model:value="createForm.is_default" />
          <span class="td-switch-hint">
            开启后，该项目下执行用例默认合并本物料集
          </span>
        </n-form-item>
        <n-form-item
          v-if="createForm.scope === 'environment'"
          label="关联环境"
          path="environment_id"
          required
        >
          <n-select
            v-model:value="createForm.environment_id"
            :options="envOptions"
            :loading="envLoading"
            :placeholder="envOptions.length ? '请选择环境' : '当前项目还没有环境，请先到 UI 自动化 → 环境管理 创建'"
            :disabled="!envOptions.length"
            clearable
          />
        </n-form-item>
        <n-form-item label="分类">
          <n-input
            v-model:value="createForm.category"
            placeholder="可选，用于分组展示"
          />
        </n-form-item>
        <n-form-item label="描述">
          <n-input
            v-model:value="createForm.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="补充用途、场景、负责人等"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="td-modal-footer">
          <n-button @click="createVisible = false">取消</n-button>
          <n-button
            type="primary"
            :loading="creating"
            @click="handleCreate"
          >
            创建并进入编辑
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NAlert,
  NButton,
  NEllipsis,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NModal,
  NPagination,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NScrollbar,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";

import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import {
  listSetsApi,
  createSetApi,
  deleteSetApi,
  SCOPE_META,
  displayCategoryLabel,
} from "@/services/testData";
import type {
  TestDataSet,
  DataSetScope,
  SetCreateParams,
} from "@/services/testData";
import { listEnvironmentsApi } from "@/services/uiAutomation";
import { useProjectStore } from "@/stores/project";
import { usePermission } from "@/composables/usePermission";

const router = useRouter();
const message = useMessage();
const projectStore = useProjectStore();
const { has } = usePermission();

const canEdit = computed(() => has("test_data:edit"));
const projectId = computed(() => projectStore.currentProjectId);

// ─── scope 导航 ──────────────────────────────────────────────────────

const scopeTabs: {
  key: DataSetScope;
  label: string;
  icon: string;
  description: string;
}[] = [
  {
    key: "project",
    ...{
      label: SCOPE_META.project.label,
      icon: SCOPE_META.project.icon,
      description: SCOPE_META.project.description,
    },
  },
  {
    key: "environment",
    ...{
      label: SCOPE_META.environment.label,
      icon: SCOPE_META.environment.icon,
      description: SCOPE_META.environment.description,
    },
  },
  {
    key: "personal",
    ...{
      label: SCOPE_META.personal.label,
      icon: SCOPE_META.personal.icon,
      description: SCOPE_META.personal.description,
    },
  },
];

const activeScope = ref<DataSetScope>("project");
const countByScope = ref<Record<DataSetScope, number>>({
  project: 0,
  environment: 0,
  personal: 0,
});

// ─── 列表加载 ────────────────────────────────────────────────────────

const loading = ref(false);
const sets = ref<TestDataSet[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = 30;
const searchText = ref("");

async function fetchList() {
  if (!projectId.value) {
    sets.value = [];
    total.value = 0;
    countByScope.value = { project: 0, environment: 0, personal: 0 };
    return;
  }
  loading.value = true;
  try {
    const res = await listSetsApi(projectId.value, {
      scope: activeScope.value,
      page: currentPage.value,
      page_size: pageSize,
    });
    if (res.success) {
      sets.value = res.data.items;
      total.value = res.data.total;
    }
    // 并行拉其它 scope 的计数（只要 total 不要具体内容）
    refreshCounts().catch(() => {
      /* 忽略：计数失败不应阻塞主流程 */
    });
  } catch (err) {
    message.error(err instanceof Error ? err.message : "加载物料集失败");
  } finally {
    loading.value = false;
  }
}

async function refreshCounts() {
  if (!projectId.value) return;
  const scopes: DataSetScope[] = ["project", "environment", "personal"];
  const results = await Promise.all(
    scopes.map((s) =>
      listSetsApi(projectId.value!, { scope: s, page: 1, page_size: 1 })
        .then((r) => (r.success ? r.data.total : 0))
        .catch(() => 0),
    ),
  );
  countByScope.value = {
    project: results[0],
    environment: results[1],
    personal: results[2],
  };
}

const filteredSets = computed(() => {
  const q = searchText.value.trim().toLowerCase();
  if (!q) return sets.value;
  return sets.value.filter((s) => {
    if (s.name.toLowerCase().includes(q)) return true;
    if ((s.description ?? "").toLowerCase().includes(q)) return true;
    if ((s.category ?? "").toLowerCase().includes(q)) return true;
    return false;
  });
});

// ─── 新建弹窗 ────────────────────────────────────────────────────────

const createVisible = ref(false);
const createFormRef = ref<FormInst | null>(null);
const creating = ref(false);
const envLoading = ref(false);
const envOptions = ref<{ label: string; value: string }[]>([]);
const createForm = ref<{
  name: string;
  description: string;
  category: string;
  scope: DataSetScope;
  is_default: boolean;
  environment_id: string | null;
}>({
  name: "",
  description: "",
  category: "",
  scope: "project",
  is_default: false,
  environment_id: null,
});
const createRules: FormRules = {
  name: [
    { required: true, message: "请填写物料集名称", trigger: "blur" },
    { min: 1, max: 100, message: "长度 1-100" },
  ],
  scope: [{ required: true, message: "请选择可见范围", trigger: "change" }],
  environment_id: [
    {
      required: true,
      trigger: ["blur", "change"],
      validator: (_rule: unknown, value: string | null | undefined) => {
        if (createForm.value.scope === "environment" && !value) {
          return new Error("环境专属物料集必须选择关联环境");
        }
        return true;
      },
    },
  ],
};

async function loadEnvOptions() {
  if (!projectId.value) return;
  envLoading.value = true;
  try {
    const res = await listEnvironmentsApi(projectId.value, { page: 1, page_size: 200 });
    if (res.success) {
      envOptions.value = res.data.items.map((e) => ({
        label: e.name,
        value: e.id,
      }));
    }
  } catch {
    /* 不阻塞；提示依赖 placeholder 文案 */
  } finally {
    envLoading.value = false;
  }
}

function openCreateDialog() {
  createForm.value = {
    name: "",
    description: "",
    category: "",
    scope: activeScope.value,
    is_default: false,
    environment_id: null,
  };
  createVisible.value = true;
  if (activeScope.value === "environment") {
    loadEnvOptions();
  }
}

watch(
  () => createForm.value.scope,
  (s) => {
    if (s === "environment" && envOptions.value.length === 0) {
      loadEnvOptions();
    }
  },
);

async function handleCreate() {
  await createFormRef.value?.validate();
  if (!projectId.value) return;
  creating.value = true;
  try {
    const payload: SetCreateParams = {
      name: createForm.value.name,
      description: createForm.value.description || null,
      category: createForm.value.category || null,
      scope: createForm.value.scope,
      environment_id:
        createForm.value.scope === "environment"
          ? createForm.value.environment_id
          : null,
      is_default:
        createForm.value.scope === "project"
          ? createForm.value.is_default
          : false,
    };
    const res = await createSetApi(projectId.value, payload);
    if (res.success) {
      message.success("物料集已创建");
      createVisible.value = false;
      activeScope.value = res.data.scope as DataSetScope;
      await fetchList();
      // 自动跳到编辑页添加条目
      router.push({
        name: "TestDataSetEditor",
        params: { projectId: projectId.value, setId: res.data.id },
      });
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "创建失败");
  } finally {
    creating.value = false;
  }
}

// ─── 操作 ────────────────────────────────────────────────────────────

function openEditor(set: TestDataSet) {
  router.push({
    name: "TestDataSetEditor",
    params: { projectId: set.project_id, setId: set.id },
  });
}

async function handleDelete(set: TestDataSet) {
  try {
    const res = await deleteSetApi(set.id);
    if (res.success) {
      message.success("物料集已删除");
      await fetchList();
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "删除失败");
  }
}

function handlePageChange(p: number) {
  currentPage.value = p;
  fetchList();
}

// ─── 空态文案 ────────────────────────────────────────────────────────

const scopeEmptyIcon = computed(() => {
  const s = activeScope.value;
  if (s === "project") return "i-carbon-folder-add";
  if (s === "environment") return "i-carbon-cloud-data-ops";
  return "i-carbon-user-follow";
});
const scopeEmptyLabel = computed(() => SCOPE_META[activeScope.value].label);
const scopeEmptyTitle = computed(() => `还没有${scopeEmptyLabel.value}物料集`);
const scopeEmptyDesc = computed(
  () => SCOPE_META[activeScope.value].description,
);

// ─── 时间格式化 ──────────────────────────────────────────────────────

function formatRelative(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return new Date(iso).toLocaleString();
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return "刚刚";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(iso).toLocaleDateString();
}

// ─── 生命周期 ────────────────────────────────────────────────────────

onMounted(() => {
  if (projectStore.projects.length === 0) {
    projectStore.fetchProjects().finally(fetchList);
  } else {
    fetchList();
  }
});

watch(() => projectStore.currentProjectId, () => {
  currentPage.value = 1;
  searchText.value = "";
  fetchList();
});

watch(activeScope, () => {
  currentPage.value = 1;
  searchText.value = "";
  fetchList();
});
</script>

<style scoped>
.td-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 16px;
  align-items: start;
}

@media (max-width: 960px) {
  .td-layout {
    grid-template-columns: 1fr;
  }
}

.td-sidebar {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 8px;
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 120px);
}

.td-tabs {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.td-tab {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    color var(--duration-fast) var(--easing-standard);
}

.td-tab:hover {
  background: var(--bg-active);
}

.td-tab--active {
  background: var(--brand-gradient-soft);
  color: var(--brand-primary);
}

.td-tab--active .td-tab__desc {
  color: var(--text-secondary);
}

.td-tab__icon {
  font-size: 20px;
  color: var(--brand-primary);
  flex-shrink: 0;
}

.td-tab__body {
  flex: 1;
  min-width: 0;
}

.td-tab__title {
  font-size: 13.5px;
  font-weight: 600;
}

.td-tab__desc {
  font-size: 11.5px;
  color: var(--text-tertiary);
  line-height: 1.4;
  margin-top: 2px;
}

.td-tab__badge {
  flex-shrink: 0;
  font-size: 12px;
  padding: 0 8px;
  height: 20px;
  line-height: 20px;
  border-radius: 10px;
  background: var(--bg-active);
  color: var(--text-secondary);
}

.td-tab--active .td-tab__badge {
  background: var(--brand-primary);
  color: #fff;
}

.td-main {
  min-width: 0;
}

.td-main__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 12px;
}

.td-card {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    box-shadow var(--duration-fast) var(--easing-standard),
    transform var(--duration-fast) var(--easing-standard);
  min-height: 136px;
}

.td-card:hover {
  border-color: var(--brand-primary-border);
  box-shadow: 0 4px 18px -8px var(--brand-primary-shadow, rgba(0, 0, 0, 0.12));
  transform: translateY(-2px);
}

.td-card__header {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.td-card__icon {
  width: 38px;
  height: 38px;
  border-radius: var(--radius-md);
  background: var(--brand-gradient-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: var(--brand-primary);
  flex-shrink: 0;
}

.td-card__title-block {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.td-card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  min-width: 0;
  line-height: 1.3;
}

.td-card__tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.td-card__desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
  flex: 1;
}

.td-card__desc--placeholder {
  color: var(--text-tertiary);
  font-style: italic;
}

.td-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--border-subtle);
}

.td-card__updated {
  font-size: 11px;
  color: var(--text-tertiary);
}

.td-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.td-switch-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
