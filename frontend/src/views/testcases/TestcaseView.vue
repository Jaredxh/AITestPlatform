<template>
  <div class="testcase-page">
    <page-header title="测试用例" subtitle="按模块组织测试用例，支持手动编写与 AI 批量生成" icon="i-carbon-task" />

    <n-alert v-if="!projectStore.currentProjectId" type="warning" class="mb-4">
      请先在顶栏选择一个项目，再管理测试用例。
    </n-alert>

    <div v-else class="testcase-layout" :class="{ 'is-collapsed': sidebarCollapsed }">
      <aside v-if="!sidebarCollapsed" class="testcase-layout__side">
        <div class="testcase-layout__side-header">
          <div class="testcase-layout__side-title">
            <span class="i-carbon-tree-view text-brand" />
            <span>模块目录</span>
            <span v-if="totalCount > 0" class="testcase-layout__count">（{{ totalCount }}）</span>
          </div>
          <div class="testcase-layout__side-actions">
            <n-tooltip placement="top">
              <template #trigger>
                <n-button size="tiny" quaternary circle @click="handleAddRootModule">
                  <template #icon><span class="i-carbon-add" /></template>
                </n-button>
              </template>
              新建顶级模块
            </n-tooltip>
            <n-tooltip placement="top">
              <template #trigger>
                <n-button
                  size="tiny"
                  quaternary
                  circle
                  :class="{ 'text-brand': selectedModuleId == null }"
                  @click="handleShowAll"
                >
                  <template #icon><span class="i-carbon-list" /></template>
                </n-button>
              </template>
              查看全部用例
            </n-tooltip>
            <n-tooltip placement="top">
              <template #trigger>
                <n-button
                  size="tiny"
                  quaternary
                  circle
                  @click="toggleSidebar"
                >
                  <template #icon><span class="i-carbon-side-panel-close" /></template>
                </n-button>
              </template>
              收起模块栏
            </n-tooltip>
          </div>
        </div>
        <div class="testcase-layout__side-body">
          <module-tree ref="moduleTreeRef" hide-header @select="handleModuleSelect" />
        </div>
      </aside>

      <!-- 折叠态：左侧只剩一条窄竖条，竖排"模块目录"四字 + 展开按钮 -->
      <aside v-else class="testcase-layout__side-collapsed" @click="toggleSidebar">
        <n-tooltip placement="right">
          <template #trigger>
            <button type="button" class="testcase-layout__side-collapsed-btn" @click.stop="toggleSidebar">
              <span class="i-carbon-side-panel-open" />
            </button>
          </template>
          展开模块栏
        </n-tooltip>
        <div class="testcase-layout__side-collapsed-label">
          <span class="i-carbon-tree-view" />
          <span class="testcase-layout__side-collapsed-text">模块目录</span>
          <span v-if="totalCount > 0" class="testcase-layout__side-collapsed-count">{{ totalCount }}</span>
        </div>
      </aside>

      <section class="testcase-layout__main">
        <testcase-table
          ref="tableRef"
          :module-id="selectedModuleId"
          @view="handleView"
          @create="handleCreate"
          @generate="handleStartAuthoring"
          @mutated="handleTestcaseMutated"
          @execute-u-i-test="handleExecuteUITest"
        />
      </section>
    </div>

    <testcase-detail
      v-model:show="showDetail"
      :testcase-id="editingId"
      @saved="handleSaved"
    />

    <execute-dialog
      v-model:show="showExecuteDialog"
      :testcase-ids="executeTargetIds"
      @submitted="handleExecutionSubmitted"
    />

    <generate-dialog
      ref="generateDialogRef"
      v-model:show="showGenerateDialog"
      :module-id="selectedModuleId"
      @accepted="handleAccepted"
      @background="handleBackground"
      @task-snapshot="handleTaskSnapshot"
    />

    <transition-group name="fade" tag="div" class="generate-floating-stack">
      <button
        v-for="task in visibleTasks"
        :key="task.id"
        type="button"
        class="generate-floating"
        :class="{ 'is-running': task.generating }"
        @click="reopenGenerateDialog(task.id)"
      >
        <span
          :class="task.generating ? 'i-carbon-progress-bar-round' : 'i-carbon-task'"
          class="generate-floating__icon"
        />
        <div class="generate-floating__text">
          <div class="generate-floating__title">
            {{ task.generating ? "AI 用例生成中..." : `${task.cases.length} 条用例待处理` }}
          </div>
          <div v-if="task.documentName" class="generate-floating__sub">
            {{ task.documentName }}
          </div>
        </div>
        <span class="i-carbon-launch generate-floating__launch" />
      </button>
    </transition-group>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, watch } from "vue";
import { NAlert, NButton, NTooltip, useMessage } from "naive-ui";
import { useProjectStore } from "@/stores/project";
import ModuleTree from "@/components/testcases/ModuleTree.vue";
import TestcaseTable from "@/components/testcases/TestcaseTable.vue";
import TestcaseDetail from "@/components/testcases/TestcaseDetail.vue";
import GenerateDialog from "@/components/testcases/GenerateDialog.vue";
import type { GenerateTaskSnapshot } from "@/components/testcases/GenerateDialog.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import ExecuteDialog from "@/components/ui-automation/ExecuteDialog.vue";
import { listGenerationBatchesApi, getGenerationBatchApi } from "@/services/testcases";

const projectStore = useProjectStore();
const message = useMessage();

defineOptions({ name: "TestcaseView" });

const moduleTreeRef = ref<InstanceType<typeof ModuleTree>>();
const tableRef = ref<InstanceType<typeof TestcaseTable>>();
const generateDialogRef = ref<InstanceType<typeof GenerateDialog>>();

const totalCount = computed(() => moduleTreeRef.value?.totalCaseCount ?? 0);

const selectedModuleId = ref<string | null>(null);
const showDetail = ref(false);
const editingId = ref<string | null>(null);
const showGenerateDialog = ref(false);

// Task 10.1：执行 UI 测试的弹窗状态。executeTargetIds 是用户在表格里勾的
// 那一组 ids 的快照——弹窗关闭时不清空，避免"再次打开默认空"的体验断裂。
const showExecuteDialog = ref(false);
const executeTargetIds = ref<string[]>([]);

function handleExecuteUITest(ids: string[]) {
  if (!projectStore.currentProjectId) {
    message.warning("请先在顶栏选择一个项目");
    return;
  }
  if (ids.length === 0) return;
  executeTargetIds.value = [...ids];
  showExecuteDialog.value = true;
}

function handleExecutionSubmitted(_executionId: string) {
  // 提交完弹窗已自动关闭并跳转监控页；这里只清理表格选中态，避免用户回来
  // 后看到上次的勾选还在。
  tableRef.value?.fetchTestcases();
}

// 左侧模块栏的折叠/展开状态。持久化到 localStorage，让用户跨页面/刷新
// 也能保留偏好——很多用户折叠后都是为了长期看到更多列。
const SIDEBAR_COLLAPSED_KEY = "testcase.sidebarCollapsed";
const sidebarCollapsed = ref<boolean>(
  typeof window !== "undefined" && window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1",
);

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value;
  try {
    window.localStorage.setItem(
      SIDEBAR_COLLAPSED_KEY,
      sidebarCollapsed.value ? "1" : "0",
    );
  } catch {
    /* localStorage may be unavailable in privacy mode; ignore */
  }
}

interface FloatingState {
  visible: boolean;
  generating: boolean;
  documentName?: string;
}

const floatingState = ref<FloatingState>({
  visible: false,
  generating: false,
  documentName: undefined,
});
const generateTasks = ref<GenerateTaskSnapshot[]>([]);

const visibleTasks = computed(() =>
  generateTasks.value.filter((task) => task.generating || task.cases.length > 0),
);

/**
 * GenerateDialog 通过 emit("background", { generating, documentName }) 通知后台状态变化。
 * 这里在 generating=true 时无条件展示浮动条；generating=false 时根据 visible 决定保留还是清除。
 */
function handleBackground(payload: { generating: boolean; documentName?: string }) {
  if (payload.generating) {
    floatingState.value = {
      visible: true,
      generating: true,
      documentName: payload.documentName,
    };
    return;
  }
  // 生成结束：若浮窗已显示，则改为"已完成"提示，待用户点击查看；否则不显示。
  if (floatingState.value.visible) {
    floatingState.value = {
      visible: true,
      generating: false,
      documentName: payload.documentName ?? floatingState.value.documentName,
    };
  }
}

/**
 * "新建 AI 生成"入口。唯一会把 dialog 切到 config 视图（选择文档/模块/LLM）
 * 的路径；其它所有入口（浮窗点击、onActivated 恢复等）都走 restoreTask，
 * 保证 viewMode 只进入 'generating' / 'preview' / 'finished'，永远不会
 * 掉到 config。
 */
function handleStartAuthoring() {
  generateDialogRef.value?.startAuthoring();
}

async function reopenGenerateDialog(taskId?: string) {
  // 1) 本地缓存命中：直接 restore，秒开。
  if (taskId) {
    const task = generateTasks.value.find((t) => t.id === taskId);
    if (task) {
      // 本地 task 如果本身就是"空壳"（生成失败 / 已全部入库），直接提示
      // 用户；不要进 restoreTask，否则 dialog 会落到"选择文档"的初始视图。
      if (!task.generating && task.cases.length === 0 && !task.errorMsg) {
        message.info("该 AI 生成任务已结束，没有可处理的候选用例");
        generateTasks.value = generateTasks.value.filter((t) => t.id !== taskId);
        return;
      }
      generateDialogRef.value?.restoreTask(task);
      return;
    }
    // 2) 本地没找到（可能被轮询刚过滤掉 / 刷新后尚未恢复）：去后端查一次，
    //    避免用户看到"选项弹窗"这种文不对题的 UI。
    try {
      const res = await getGenerationBatchApi(taskId);
      if (res.success) {
        const b = res.data;
        const snapshot: GenerateTaskSnapshot = {
          id: b.id,
          documentName: b.document_name || undefined,
          generating: b.status === "generating",
          streamContent:
            b.status === "generating"
              ? "任务仍在后台生成中，实时流马上恢复…"
              : "",
          batchId: b.id,
          cases: b.testcases.map((tc) => ({ ...tc, _selected: true })),
          moduleId: b.module_id ?? null,
          errorMsg: b.status === "failed" ? "生成失败，请重新发起" : "",
        };
        // 后端返回的任务如果没有候选用例且没在生成也没失败，说明已被全部入库，
        // 浮窗已经"过期"，告诉用户就好，不要 restoreTask。
        if (
          !snapshot.generating &&
          snapshot.cases.length === 0 &&
          !snapshot.errorMsg
        ) {
          message.info("该 AI 生成任务已完成并全部入库，无可处理候选用例");
          generateTasks.value = generateTasks.value.filter((t) => t.id !== taskId);
          return;
        }
        handleTaskSnapshot(snapshot);
        generateDialogRef.value?.restoreTask(snapshot);
        return;
      }
    } catch {
      /* ignore, fall through to message */
    }
    // 3) 后端也拿不到（可能已被清理）：提示用户并移除悬浮按钮，
    //    绝不弹出"选择文档"的新建窗口——那是另一个场景。
    message.info("该 AI 生成任务已结束或已被清理");
    generateTasks.value = generateTasks.value.filter((t) => t.id !== taskId);
    return;
  }
  // 没有传 taskId 才是真正的"新建"入口。
  handleStartAuthoring();
  floatingState.value = { visible: false, generating: false };
}

function handleTaskSnapshot(task: GenerateTaskSnapshot) {
  if (!task.generating && task.cases.length === 0) {
    generateTasks.value = generateTasks.value.filter((t) => t.id !== task.id);
    ensureTaskPolling();
    return;
  }
  const idx = generateTasks.value.findIndex((t) => t.id === task.id);
  if (idx >= 0) {
    generateTasks.value[idx] = task;
  } else {
    generateTasks.value.unshift(task);
  }
  ensureTaskPolling();
}

async function restoreGenerationTasks() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  const res = await listGenerationBatchesApi(projectId);
  if (!res.success) return;
  for (const batch of res.data) {
    handleTaskSnapshot({
      id: batch.id,
      documentName: batch.document_name || undefined,
      generating: batch.status === "generating",
      streamContent:
        batch.status === "generating"
          ? `任务正在后台生成中，完成后悬浮窗会自动显示候选用例...`
          : "",
      batchId: batch.id,
      cases: batch.testcases.map((tc) => ({ ...tc, _selected: true })),
      moduleId: batch.module_id ?? null,
      errorMsg: batch.status === "failed" ? "生成失败，请重新发起" : "",
    });
  }
  ensureTaskPolling();
}

// 统一的后台任务轮询：只要还有 generating 任务就保持 1s 一次的轻量刷新，
// 这样悬浮窗的标题（生成中/几条待处理）和数据能在不打开弹窗时也自动更新。
let taskPollTimer: ReturnType<typeof window.setInterval> | null = null;

function ensureTaskPolling() {
  const hasGenerating = generateTasks.value.some((t) => t.generating);
  if (!hasGenerating) {
    if (taskPollTimer) {
      window.clearInterval(taskPollTimer);
      taskPollTimer = null;
    }
    return;
  }
  if (taskPollTimer) return;
  taskPollTimer = window.setInterval(pollGeneratingTasks, 1000);
}

async function pollGeneratingTasks() {
  const generating = generateTasks.value.filter((t) => t.generating);
  if (generating.length === 0) {
    ensureTaskPolling();
    return;
  }
  for (const task of generating) {
    try {
      const res = await getGenerationBatchApi(task.batchId);
      if (!res.success) continue;
      const batch = res.data;
      if (batch.status === "generating") continue;
      const cases = batch.testcases.map((tc) => ({ ...tc, _selected: true }));
      const updated: GenerateTaskSnapshot = {
        ...task,
        generating: false,
        cases,
        moduleId: batch.module_id ?? task.moduleId,
        streamContent: "",
        errorMsg: batch.status === "failed" ? "生成失败，请重新发起" : "",
      };
      handleTaskSnapshot(updated);
      if (batch.status === "completed" && cases.length > 0) {
        message.success(
          `AI 已完成「${batch.document_name || "文档"}」的 ${cases.length} 条用例，点击右下角悬浮窗查看。`,
        );
      }
    } catch {
      /* transient network errors are tolerable; retry on next tick */
    }
  }
  ensureTaskPolling();
}

function handleModuleSelect(moduleId: string | null) {
  selectedModuleId.value = moduleId;
}

function handleShowAll() {
  selectedModuleId.value = null;
}

function handleView(id: string) {
  editingId.value = id;
  showDetail.value = true;
}

function handleCreate() {
  editingId.value = null;
  showDetail.value = true;
}

function handleSaved() {
  tableRef.value?.fetchTestcases();
  moduleTreeRef.value?.fetchModules();
}

/**
 * 用例发生删除/批量变更时，刷新左侧模块树以同步每个模块的用例数量。
 * 之前只刷新了当前表格，模块计数要用户手动刷新页面才能更新。
 */
function handleTestcaseMutated() {
  moduleTreeRef.value?.fetchModules();
}

/**
 * 入库后：若 AI 生成时选中的模块与当前左侧选中的模块不一致，
 * 自动把视图切到目标模块，避免用户以为"用例没生成"——这是第三轮反馈的核心 bug。
 */
function handleAccepted(payload?: { module_id: string | null }) {
  if (payload && payload.module_id !== undefined) {
    selectedModuleId.value = payload.module_id;
  }
  // Refresh tree (module counts) + list (newly inserted cases) right away so the
  // user sees the data land in the correct module without a manual F5.
  handleSaved();
  const snapshot = generateDialogRef.value?.currentSnapshot();
  if (snapshot) {
    handleTaskSnapshot(snapshot);
  }
}

function handleAddRootModule() {
  moduleTreeRef.value?.openAddRootDialog();
}

// 切换项目时，模块/筛选都属于"上一个项目"上下文，必须重置，
// 否则会用旧项目的 module_id 去查新项目，导致列表"看上去是空的"。
watch(
  () => projectStore.currentProjectId,
  async () => {
    selectedModuleId.value = null;
    moduleTreeRef.value?.clearSelection();
    await nextTick();
    tableRef.value?.resetFilters();
  },
);

onActivated(() => {
  // 从其他页面返回时，保留正在生成/待确认的弹窗状态，同时刷新左树和列表。
  moduleTreeRef.value?.fetchModules();
  tableRef.value?.fetchTestcases();
  restoreGenerationTasks();
});

onDeactivated(() => {
  // 切到其他页面时，正在生成或已有未处理候选用例的任务自动转后台悬浮。
  generateDialogRef.value?.minimizeToBackground();
});

onMounted(() => {
  restoreGenerationTasks();
});

onBeforeUnmount(() => {
  if (taskPollTimer) {
    window.clearInterval(taskPollTimer);
    taskPollTimer = null;
  }
});
</script>

<style scoped>
.testcase-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.testcase-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 16px;
  flex: 1;
  min-height: 0;
  /* 关键：限制总高度，让左右两栏各自 overflow 滚动 */
  height: calc(100vh - 240px);
  transition: grid-template-columns 0.2s ease;
}

.testcase-layout.is-collapsed {
  grid-template-columns: 36px 1fr;
}

.testcase-layout__side {
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  min-height: 0;
}

.testcase-layout__side-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 10px 14px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-card);
  flex-shrink: 0;
}

.testcase-layout__side-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.testcase-layout__side-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.testcase-layout__side-body {
  flex: 1;
  padding: 6px 4px 12px;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}

.testcase-layout__count {
  color: var(--text-tertiary);
  font-weight: 500;
  font-size: 12px;
}

/* ── 折叠态侧栏：一条窄竖条，竖排标题 + 展开按钮 ── */
.testcase-layout__side-collapsed {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 0;
  gap: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: background 0.15s ease;
  min-height: 0;
  overflow: hidden;
}

.testcase-layout__side-collapsed:hover {
  background: var(--bg-subtle, #f5f7fa);
}

.testcase-layout__side-collapsed-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 16px;
}

.testcase-layout__side-collapsed-btn:hover {
  background: var(--bg-card);
  color: var(--brand-primary);
}

.testcase-layout__side-collapsed-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.testcase-layout__side-collapsed-text {
  /* 让"模块目录"四字一字一行，纵向排列 */
  writing-mode: vertical-rl;
  letter-spacing: 4px;
  user-select: none;
}

.testcase-layout__side-collapsed-count {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
  padding: 1px 6px;
  background: var(--bg-subtle, #eef0f3);
  border-radius: 999px;
}

.testcase-layout__main {
  min-width: 0;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.text-brand {
  color: var(--brand-primary);
}

.generate-floating-stack {
  position: fixed;
  right: 24px;
  bottom: 32px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
  z-index: 1000;
}

.generate-floating {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px 10px 14px;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  box-shadow: var(--shadow-md, 0 8px 24px rgba(0, 0, 0, 0.12));
  cursor: pointer;
  font-family: inherit;
  color: var(--text-primary);
  transition: transform var(--duration-fast) var(--easing-standard);
}

.generate-floating:hover {
  transform: translateY(-2px);
}

.generate-floating__icon {
  font-size: 22px;
  color: var(--brand-primary);
}

.generate-floating.is-running .generate-floating__icon {
  animation: spin-soft 1.6s linear infinite;
}

@keyframes spin-soft {
  to { transform: rotate(360deg); }
}

.generate-floating__text {
  text-align: left;
  line-height: 1.2;
}

.generate-floating__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.generate-floating__sub {
  font-size: 11px;
  color: var(--text-tertiary);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.generate-floating__launch {
  color: var(--text-tertiary);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .testcase-layout {
    grid-template-columns: 1fr;
    height: auto;
  }
  .testcase-layout.is-collapsed {
    grid-template-columns: 1fr;
  }
}
</style>
