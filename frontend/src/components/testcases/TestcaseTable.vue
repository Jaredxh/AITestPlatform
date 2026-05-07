<template>
  <div class="testcase-table-wrap">
    <div class="testcase-table-toolbar">
      <div class="testcase-table-toolbar__left">
        <n-input
          v-model:value="searchText"
          placeholder="搜索用例标题"
          clearable
          class="w-52"
          @update:value="debouncedSearch"
        >
          <template #prefix><span class="i-carbon-search text-gray-400" /></template>
        </n-input>
        <n-select
          v-model:value="filterPriority"
          :options="priorityOptions"
          placeholder="优先级"
          clearable
          class="w-32"
          :consistent-menu-width="false"
          @update:value="handleFilterChange"
        />
        <n-select
          v-model:value="filterStatus"
          :options="statusOptions"
          placeholder="状态"
          clearable
          class="w-32"
          :consistent-menu-width="false"
          @update:value="handleFilterChange"
        />
        <n-select
          v-model:value="filterSource"
          :options="sourceOptions"
          placeholder="来源"
          clearable
          class="w-36"
          :consistent-menu-width="false"
          @update:value="handleFilterChange"
        />
        <n-select
          v-model:value="filterExecResult"
          :options="execResultFilterOptions"
          placeholder="执行结果"
          clearable
          class="w-36"
          :consistent-menu-width="false"
          @update:value="handleFilterChange"
        />
      </div>
      <div class="testcase-table-toolbar__right">
        <!--
          按钮编排原则：
          - AI 生成 = 主行动（primary 实心），放第一位形成视觉锚点；
          - 其余三个（新建用例 / 导入导出 / 执行历史）= 同级次行动，用同一套
            默认描边样式（``<n-button>`` 无 prop），以避免"每个按钮长得都不一样"
            的杂乱感；
          - 顺序按"创建 → 批量管理 → 历史回看"的工作流自左向右排：
            AI 生成 · 新建用例 · 导入 / 导出 · 执行历史
        -->
        <n-button type="primary" @click="$emit('generate')">
          <template #icon><span class="i-carbon-magic-wand" /></template>
          AI 生成
        </n-button>
        <n-button @click="$emit('create')">
          <template #icon><span class="i-carbon-add" /></template>
          新建用例
        </n-button>
        <n-dropdown
          trigger="click"
          :options="ioMenuOptions"
          @select="handleIoMenuSelect"
        >
          <n-button :disabled="!projectStore.currentProjectId">
            <template #icon><span class="i-carbon-data-table-reference" /></template>
            导入 / 导出
          </n-button>
        </n-dropdown>
        <n-button
          :disabled="!projectStore.currentProjectId"
          @click="goExecutionHistory"
        >
          <template #icon><span class="i-carbon-recording" /></template>
          执行历史
        </n-button>
      </div>
    </div>

    <import-dialog
      v-model:show="showImportDialog"
      :project-id="projectStore.currentProjectId"
      @download-template="handleDownloadTemplate"
      @imported="handleImported"
    />

    <transition name="fade-slide">
      <div v-if="checkedRowKeys.length > 0" class="testcase-batch-bar">
        <span class="testcase-batch-bar__count">
          已选 <strong>{{ checkedRowKeys.length }}</strong> 条用例
        </span>
        <div class="testcase-batch-bar__actions">
          <!-- Task 10.1：执行 UI 测试入口（高优先级，放最前） -->
          <n-button
            size="small"
            type="primary"
            :disabled="!canRunUITest"
            @click="handleExecuteUITest"
          >
            <template #icon><span class="i-carbon-play-filled-alt" /></template>
            执行 UI 测试
          </n-button>
          <n-popselect
            :value="null"
            :options="batchStatusOptions"
            @update:value="handleBatchStatus"
          >
            <n-button size="small" type="warning" ghost>
              <template #icon><span class="i-carbon-edit" /></template>
              批量改状态
            </n-button>
          </n-popselect>
          <n-popconfirm @positive-click="handleBatchDelete">
            <template #trigger>
              <n-button size="small" type="error" ghost>
                <template #icon><span class="i-carbon-trash-can" /></template>
                批量删除
              </n-button>
            </template>
            确认删除选中的 {{ checkedRowKeys.length }} 条用例？此操作不可恢复。
          </n-popconfirm>
          <n-button size="small" quaternary @click="checkedRowKeys = []">
            取消选择
          </n-button>
        </div>
      </div>
    </transition>

    <div class="testcase-table-body">
      <n-spin :show="loading" class="h-full">
        <n-data-table
          v-if="testcases.length > 0 || loading"
          v-model:checked-row-keys="checkedRowKeys"
          :columns="columns"
          :data="testcases"
          :row-key="(row: TestcaseListItem) => row.id"
          :bordered="false"
          striped
          @update:sorter="handleSorterChange"
        />
        <app-empty
          v-else-if="!loading && hasSearchOrFilter"
          icon="i-carbon-search"
          title="没有匹配的用例"
          description="当前筛选条件下没有用例，可调整筛选或清空后再试。"
          class="mt-12"
        >
          <template #actions>
            <n-button @click="resetFilters">清空筛选</n-button>
          </template>
        </app-empty>
        <app-empty
          v-else-if="!loading"
          icon="i-carbon-task"
          title="暂无测试用例"
          :description="moduleId ? '该模块下还没有测试用例，可手动创建或让 AI 基于需求文档批量生成。' : '点击右上角开始创建或让 AI 批量生成。'"
          class="mt-12"
        >
          <template #actions>
            <n-button type="primary" @click="$emit('generate')">
              <template #icon><span class="i-carbon-magic-wand" /></template>
              AI 生成用例
            </n-button>
            <n-button @click="$emit('create')">手动创建</n-button>
          </template>
        </app-empty>
      </n-spin>
    </div>

    <div v-if="total > 0" class="testcase-table-pager">
      <n-text depth="3" class="text-xs">共 {{ total }} 条用例</n-text>
      <n-pagination
        v-model:page="currentPage"
        :item-count="total"
        :page-sizes="pageSizeOptions"
        :page-size="pageSize"
        show-size-picker
        @update:page="handlePageChange"
        @update:page-size="handlePageSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, h, watch, computed } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NDataTable,
  NDropdown,
  NInput,
  NPagination,
  NPopconfirm,
  NPopselect,
  NSelect,
  NSpin,
  NTag,
  NText,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, DropdownOption } from "naive-ui";
import AppEmpty from "@/components/common/AppEmpty.vue";
import ImportDialog from "@/components/testcases/ImportDialog.vue";
import {
  listTestcasesApi,
  deleteTestcaseApi,
  downloadTestcaseTemplateApi,
  exportTestcasesApi,
  updateTestcaseApi,
} from "@/services/testcases";
import type {
  ExecResult,
  TestcaseImportReport,
  TestcaseListItem,
} from "@/services/testcases";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";

const props = defineProps<{
  moduleId: string | null;
}>();

const emit = defineEmits<{
  (e: "view", id: string): void;
  (e: "create"): void;
  (e: "generate"): void;
  // 任何会影响"模块下用例数量"的变更（删除、批量删除、批量移动模块等）
  // 都通过这个事件通知父组件去刷新左侧模块树的计数。
  (e: "mutated"): void;
  // Task 10.1：批量执行 UI 自动化测试。父组件接住后弹出 ExecuteDialog。
  (e: "executeUITest", ids: string[]): void;
}>();

const message = useMessage();
const projectStore = useProjectStore();
const authStore = useAuthStore();
const router = useRouter();

function goExecutionHistory() {
  if (!projectStore.currentProjectId) return;
  router.push({
    name: "UIExecutionHistory",
    params: { projectId: projectStore.currentProjectId },
  });
}

const loading = ref(false);
const testcases = ref<TestcaseListItem[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);
const pageSizeOptions = [10, 20, 50, 100];

const checkedRowKeys = ref<string[]>([]);
const updatingExecId = ref<string | null>(null);

const searchText = ref("");
const filterPriority = ref<string | null>(null);
const filterStatus = ref<string | null>(null);
const filterSource = ref<string | null>(null);
const filterExecResult = ref<string | null>(null);

const priorityOptions = [
  { label: "高", value: "high" },
  { label: "中", value: "medium" },
  { label: "低", value: "low" },
];

const statusOptions = [
  { label: "有效", value: "active" },
  { label: "草稿", value: "draft" },
  { label: "废弃", value: "deprecated" },
];

const sourceOptions = [
  { label: "手动创建", value: "manual" },
  { label: "AI 生成", value: "ai_generated" },
];

const execResultMap: Record<ExecResult, { label: string; type: "default" | "success" | "error" | "warning" }> = {
  not_run: { label: "未执行", type: "default" },
  passed: { label: "通过", type: "success" },
  failed: { label: "失败", type: "error" },
  blocked: { label: "阻塞", type: "warning" },
};

const execResultOptions = [
  { label: "未执行", value: "not_run" },
  { label: "通过", value: "passed" },
  { label: "失败", value: "failed" },
  { label: "阻塞", value: "blocked" },
];

const execResultFilterOptions = execResultOptions;

const priorityMap: Record<string, { label: string; type: "error" | "warning" | "info" }> = {
  high: { label: "高", type: "error" },
  medium: { label: "中", type: "warning" },
  low: { label: "低", type: "info" },
};

const statusMap: Record<string, { label: string; type: "success" | "default" | "error" }> = {
  active: { label: "有效", type: "success" },
  draft: { label: "草稿", type: "default" },
  deprecated: { label: "废弃", type: "error" },
};

const batchStatusOptions = [
  { label: "标记为有效", value: "active" },
  { label: "标记为草稿", value: "draft" },
  { label: "标记为废弃", value: "deprecated" },
];

const columns: DataTableColumns<TestcaseListItem> = [
  { type: "selection", fixed: "left" },
  {
    title: "编号",
    key: "display_id",
    width: 96,
    fixed: "left",
    render(row) {
      const text = row.display_id || (row.case_no ? `TC-${String(row.case_no).padStart(4, "0")}` : "TC-?");
      return h(
        "span",
        {
          class: "testcase-display-id",
          title: text,
        },
        text,
      );
    },
  },
  {
    title: "用例标题",
    key: "title",
    ellipsis: { tooltip: true },
    minWidth: 200,
    render(row) {
      return h(
        "a",
        {
          class: "text-blue-500 cursor-pointer hover:underline",
          onClick: () => emit("view", row.id),
        },
        row.title,
      );
    },
  },
  {
    title: "所属模块",
    key: "module_name",
    width: 140,
    ellipsis: { tooltip: true },
    render(row) {
      return row.module_name || h(NText, { depth: 3 }, () => "未分类");
    },
  },
  {
    title: "优先级",
    key: "priority",
    width: 80,
    render(row) {
      const p = priorityMap[row.priority] || { label: row.priority, type: "info" as const };
      return h(NTag, { size: "small", type: p.type, bordered: false }, () => p.label);
    },
  },
  {
    title: "状态",
    key: "status",
    width: 80,
    render(row) {
      const s = statusMap[row.status] || { label: row.status, type: "default" as const };
      return h(NTag, { size: "small", type: s.type, bordered: false }, () => s.label);
    },
  },
  {
    title: "来源",
    key: "source",
    width: 90,
    render(row) {
      const isAI = row.source === "ai_generated";
      return h(
        NTag,
        { size: "small", type: isAI ? "success" : "default", bordered: false },
        () => (isAI ? "AI 生成" : "手动"),
      );
    },
  },
  {
    title: "执行结果",
    key: "exec_result",
    width: 110,
    render(row) {
      const style = execResultMap[row.exec_result] || execResultMap.not_run;
      const isUpdating = updatingExecId.value === row.id;
      return h(
        NPopselect,
        {
          value: row.exec_result,
          options: execResultOptions,
          trigger: "click",
          onUpdateValue: (v: string) => handleQuickExecResult(row.id, v),
          disabled: isUpdating,
        },
        {
          default: () =>
            h(
              NTag,
              {
                size: "small",
                type: style.type,
                bordered: false,
                style: { cursor: isUpdating ? "wait" : "pointer" },
              },
              () => (isUpdating ? "更新中…" : style.label),
            ),
        },
      );
    },
  },
  {
    title: "创建者",
    key: "creator_name",
    width: 90,
    render(row) {
      return row.creator_name || "-";
    },
  },
  {
    title: "更新时间",
    key: "updated_at",
    width: 160,
    render(row) {
      return new Date(row.updated_at).toLocaleString("zh-CN");
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 160,
    render(row) {
      return h("div", { class: "flex gap-1" }, [
        h(
          NButton,
          { size: "tiny", quaternary: true, onClick: () => emit("view", row.id) },
          { default: () => "编辑" },
        ),
        h(
          NPopselect,
          {
            value: row.status,
            options: statusOptions.map((s) => ({ label: s.label, value: s.value })),
            onUpdateValue: (v: string) => handleQuickStatus(row.id, v),
          },
          {
            default: () =>
              h(
                NButton,
                { size: "tiny", quaternary: true, type: "info" },
                { default: () => "改状态" },
              ),
          },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(row.id) },
          {
            trigger: () =>
              h(
                NButton,
                { size: "tiny", quaternary: true, type: "error" },
                { default: () => "删除" },
              ),
            default: () => `确认删除「${row.title}」？`,
          },
        ),
      ]);
    },
  },
];

let searchTimer: ReturnType<typeof setTimeout>;
let requestSeq = 0;
function debouncedSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentPage.value = 1;
    fetchTestcases();
  }, 300);
}

function handleFilterChange() {
  currentPage.value = 1;
  fetchTestcases();
}

function handlePageChange(page: number) {
  currentPage.value = page;
  fetchTestcases();
}

function handlePageSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
  fetchTestcases();
}

function handleSorterChange() {
  fetchTestcases();
}

async function fetchTestcases() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;

  const seq = ++requestSeq;
  loading.value = true;
  try {
    const res = await listTestcasesApi(projectId, {
      page: currentPage.value,
      page_size: pageSize.value,
      module_id: props.moduleId || undefined,
      priority: filterPriority.value || undefined,
      status: filterStatus.value || undefined,
      source: filterSource.value || undefined,
      exec_result: filterExecResult.value || undefined,
      search: searchText.value || undefined,
    });
    if (seq !== requestSeq) return;
    if (res.success) {
      if (res.data.total > 0 && res.data.items.length === 0 && currentPage.value > 1) {
        currentPage.value = 1;
        await fetchTestcases();
        return;
      }
      testcases.value = res.data.items;
      total.value = res.data.total;
      // 翻页/筛选后清理跨页选择
      checkedRowKeys.value = checkedRowKeys.value.filter((id) =>
        res.data.items.some((t) => t.id === id),
      );
    }
  } catch {
    if (seq !== requestSeq) return;
    message.error("获取用例列表失败");
  } finally {
    if (seq === requestSeq) {
      loading.value = false;
    }
  }
}

async function handleBatchStatus(status: string | null) {
  if (!status || checkedRowKeys.value.length === 0) return;
  try {
    await Promise.all(
      checkedRowKeys.value.map((id) => updateTestcaseApi(id, { status })),
    );
    message.success(`已批量更新 ${checkedRowKeys.value.length} 条用例的状态`);
    checkedRowKeys.value = [];
    fetchTestcases();
  } catch {
    message.error("批量更新失败");
  }
}

// Task 10.1：批量执行 UI 测试。权限不足时按钮置灰；点击只是把选中的 id 抛
// 给父组件，父组件负责 ExecuteDialog 的展开和 ProjectStore 校验。
const canRunUITest = computed(() => authStore.hasPermission("ui_exec:run"));

function handleExecuteUITest() {
  if (!canRunUITest.value) {
    message.warning("没有 ui_exec:run 权限，无法触发 UI 自动化执行");
    return;
  }
  if (checkedRowKeys.value.length === 0) return;
  emit("executeUITest", [...checkedRowKeys.value]);
}

async function handleBatchDelete() {
  if (checkedRowKeys.value.length === 0) return;
  try {
    await Promise.all(checkedRowKeys.value.map((id) => deleteTestcaseApi(id)));
    message.success(`已删除 ${checkedRowKeys.value.length} 条用例`);
    checkedRowKeys.value = [];
    fetchTestcases();
    emit("mutated");
  } catch {
    message.error("批量删除失败");
  }
}

async function handleDelete(id: string) {
  try {
    const res = await deleteTestcaseApi(id);
    if (res.success) {
      message.success("用例已删除");
      fetchTestcases();
      emit("mutated");
    }
  } catch {
    message.error("删除失败");
  }
}

async function handleQuickStatus(id: string, status: string) {
  try {
    const res = await updateTestcaseApi(id, { status });
    if (res.success) {
      message.success("状态已更新");
      fetchTestcases();
    }
  } catch {
    message.error("更新状态失败");
  }
}

async function handleQuickExecResult(id: string, exec_result: string) {
  updatingExecId.value = id;
  try {
    const res = await updateTestcaseApi(id, { exec_result: exec_result as ExecResult });
    if (res.success) {
      const idx = testcases.value.findIndex((item) => item.id === id);
      if (idx >= 0) {
        testcases.value[idx] = {
          ...testcases.value[idx],
          exec_result: exec_result as ExecResult,
          updated_at: res.data.updated_at,
        };
      }
      message.success("执行结果已更新");
    }
  } catch {
    message.error("更新执行结果失败");
  } finally {
    updatingExecId.value = null;
  }
}

const hasSearchOrFilter = computed(
  () =>
    !!searchText.value ||
    !!filterPriority.value ||
    !!filterStatus.value ||
    !!filterSource.value ||
    !!filterExecResult.value,
);

function resetFilters() {
  searchText.value = "";
  filterPriority.value = null;
  filterStatus.value = null;
  filterSource.value = null;
  filterExecResult.value = null;
  currentPage.value = 1;
  fetchTestcases();
}

// ── 导入 / 导出 / 模板 ─────────────────────────────────────────────
//
// 三个动作收进同一个下拉，避免顶栏在多按钮 + 长筛选条件时被挤出第二行；
// 业务逻辑都在这里就近写，让 ImportDialog 自身保持"通用"形态（不依赖
// 任何全局 store）。

const showImportDialog = ref(false);
const ioBusy = ref(false);

const ioMenuOptions = computed<DropdownOption[]>(() => [
  {
    label: "下载导入模板",
    key: "template",
    icon: () => h("span", { class: "i-carbon-document-blank" }),
  },
  {
    label: "导出当前列表",
    key: "export",
    icon: () => h("span", { class: "i-carbon-export" }),
  },
  { type: "divider", key: "d1" },
  {
    label: "导入 Excel...",
    key: "import",
    icon: () => h("span", { class: "i-carbon-upload" }),
  },
]);

async function handleIoMenuSelect(key: string) {
  if (key === "template") {
    await handleDownloadTemplate();
    return;
  }
  if (key === "export") {
    await handleExport();
    return;
  }
  if (key === "import") {
    if (!projectStore.currentProjectId) {
      message.warning("请先在顶栏选择一个项目");
      return;
    }
    showImportDialog.value = true;
  }
}

async function handleDownloadTemplate() {
  if (!projectStore.currentProjectId) {
    message.warning("请先在顶栏选择一个项目");
    return;
  }
  if (ioBusy.value) return;
  ioBusy.value = true;
  try {
    const r = await downloadTestcaseTemplateApi(projectStore.currentProjectId);
    message.success(`已下载模板：${r.filename}`);
  } catch (exc) {
    const msg = exc instanceof Error ? exc.message : "模板下载失败";
    message.error(msg);
  } finally {
    ioBusy.value = false;
  }
}

async function handleExport() {
  if (!projectStore.currentProjectId) {
    message.warning("请先在顶栏选择一个项目");
    return;
  }
  if (total.value === 0) {
    message.info("当前筛选下没有用例可导出");
    return;
  }
  if (ioBusy.value) return;
  ioBusy.value = true;
  try {
    const r = await exportTestcasesApi(projectStore.currentProjectId, {
      module_id: props.moduleId || undefined,
      priority: filterPriority.value || undefined,
      status: filterStatus.value || undefined,
      source: filterSource.value || undefined,
      exec_result: filterExecResult.value || undefined,
      search: searchText.value || undefined,
    });
    message.success(`已导出 ${total.value} 条用例：${r.filename}`);
  } catch (exc) {
    const msg = exc instanceof Error ? exc.message : "导出失败";
    message.error(msg);
  } finally {
    ioBusy.value = false;
  }
}

function handleImported(report: TestcaseImportReport) {
  // 即便有部分错误，只要有 created/updated 就刷一下列表 + 通知父组件
  // 同步左侧模块树（导入可能新建了模块）
  if (report.created > 0 || report.updated > 0 || report.created_modules.length > 0) {
    fetchTestcases();
    emit("mutated");
  }
}

watch(
  () => props.moduleId,
  () => {
    currentPage.value = 1;
    fetchTestcases();
  },
);

watch(() => projectStore.currentProjectId, () => {
  currentPage.value = 1;
  fetchTestcases();
}, { immediate: true });

defineExpose({ fetchTestcases, resetFilters });
</script>

<style scoped>
.testcase-table-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.testcase-table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
}

.testcase-table-toolbar__left,
.testcase-table-toolbar__right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.testcase-batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--brand-primary-soft);
  border-bottom: 1px solid var(--brand-primary-border);
  flex-shrink: 0;
}

.testcase-batch-bar__count {
  font-size: 13px;
  color: var(--text-secondary);
}

.testcase-batch-bar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.testcase-table-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  position: relative;
}

.testcase-table-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-top: 1px solid var(--border-subtle);
  flex-shrink: 0;
  background: var(--bg-card);
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.2s ease;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.testcase-display-id {
  display: inline-block;
  padding: 1px 8px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #4b5563);
  background: var(--bg-subtle, #f3f4f6);
  border-radius: 4px;
  letter-spacing: 0.02em;
}
</style>
