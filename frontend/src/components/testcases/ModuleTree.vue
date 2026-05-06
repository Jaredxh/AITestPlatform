<template>
  <div class="module-tree">
    <n-spin :show="loading" size="small">
      <n-tree
        v-if="treeData.length > 0"
        :data="treeData"
        :selected-keys="selectedKeys"
        :node-props="nodeProps"
        :render-suffix="renderSuffix"
        block-line
        selectable
        default-expand-all
        @update:selected-keys="handleSelect"
      />
      <div v-else-if="!loading" class="module-tree__empty">
        <span class="i-carbon-folder-add text-2xl block mb-2 opacity-40" />
        <div class="module-tree__empty-text">暂无模块</div>
        <n-button size="tiny" type="primary" @click="handleAddRoot">
          <template #icon><span class="i-carbon-add" /></template>
          新建模块
        </n-button>
      </div>
    </n-spin>

    <n-dropdown
      :show="showContextMenu"
      :x="contextMenuX"
      :y="contextMenuY"
      :options="contextMenuOptions"
      placement="bottom-start"
      @select="handleContextAction"
      @clickoutside="showContextMenu = false"
    />

    <n-modal v-model:show="showNameDialog" preset="dialog" :title="nameDialogTitle">
      <n-input
        v-model:value="nameInput"
        placeholder="请输入模块名称"
        :maxlength="200"
        @keyup.enter="confirmNameDialog"
      />
      <template #action>
        <n-button @click="showNameDialog = false">取消</n-button>
        <n-button type="primary" :disabled="!nameInput.trim()" @click="confirmNameDialog">
          确定
        </n-button>
      </template>
    </n-modal>

    <!-- 入口路径编辑弹窗 -->
    <n-modal
      v-model:show="showEntryDialog"
      preset="dialog"
      title="设置模块入口路径"
      style="max-width: 480px"
    >
      <div class="module-tree__entry-form">
        <n-input
          v-model:value="entryInput"
          placeholder="例如：/admin/users 或 https://other.example.com/x"
          :maxlength="500"
          @keyup.enter="confirmEntryDialog"
        />
        <div class="module-tree__entry-hint">
          <p class="m-0">
            执行该模块下的用例时，AI 会先 <code>browser_navigate</code> 到此地址再开始操作。
          </p>
          <p class="m-0 mt-1">
            <strong>相对路径</strong>（如 <code>/admin/users</code>）会自动拼到环境
            <code>base_url</code> 上；填<strong>完整 URL</strong> 则原样使用。
          </p>
          <p class="m-0 mt-1 opacity-60">
            留空则该模块没有入口约束，由用例步骤里的自然语言决定目标地址。
          </p>
        </div>
      </div>
      <template #action>
        <n-button @click="showEntryDialog = false">取消</n-button>
        <n-button
          v-if="entryInputOriginal"
          quaternary
          type="warning"
          @click="clearEntryDialog"
        >
          清空入口
        </n-button>
        <n-button type="primary" @click="confirmEntryDialog">保存</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, h } from "vue";
import {
  NButton,
  NDropdown,
  NInput,
  NModal,
  NSpin,
  NTooltip,
  NTree,
  useMessage,
  useDialog,
} from "naive-ui";
import type { TreeOption } from "naive-ui";
import {
  getModuleTreeApi,
  createModuleApi,
  updateModuleApi,
  deleteModuleApi,
} from "@/services/testcases";
import type { ModuleTreeNode } from "@/services/testcases";
import { useProjectStore } from "@/stores/project";

defineProps<{
  hideHeader?: boolean;
}>();

const emit = defineEmits<{
  (e: "select", moduleId: string | null): void;
}>();

const message = useMessage();
const dialog = useDialog();
const projectStore = useProjectStore();

const loading = ref(false);
const modules = ref<ModuleTreeNode[]>([]);
const selectedModuleId = ref<string | null>(null);

const selectedKeys = computed(() =>
  selectedModuleId.value ? [selectedModuleId.value] : [],
);

function buildTreeData(nodes: ModuleTreeNode[]): TreeOption[] {
  return nodes.map((node) => ({
    key: node.id,
    label: `${node.name}（${node.case_count}）`,
    rawCount: node.case_count,
    rawEntryPath: node.entry_path,
    children:
      node.children.length > 0 ? buildTreeData(node.children) : undefined,
  }));
}

const treeData = computed(() => buildTreeData(modules.value));

const totalCaseCount = computed(() =>
  modules.value.reduce((acc, n) => acc + n.case_count, 0),
);

// ── 行内悬浮操作按钮（替代仅靠右键菜单） ──

function renderSuffix({ option }: { option: TreeOption }) {
  const entryPath = (option.rawEntryPath as string | null | undefined) || null;
  return h("div", { class: "module-tree__suffix" }, [
    // 已配 entry_path 时常驻显示一个小标，让用户一眼看到"这个模块有入口"
    entryPath
      ? h(
          NTooltip,
          { placement: "top" },
          {
            trigger: () =>
              h(
                "span",
                {
                  class: "module-tree__entry-badge",
                  onClick: (e: MouseEvent) => {
                    e.stopPropagation();
                    contextMenuNodeId.value = option.key as string;
                    openEntryDialog(entryPath);
                  },
                },
                [h("span", { class: "i-carbon-launch" })],
              ),
            default: () => `入口：${entryPath}`,
          },
        )
      : null,
    h(
      NTooltip,
      { placement: "top" },
      {
        trigger: () =>
          h(
            NButton,
            {
              size: "tiny",
              quaternary: true,
              circle: true,
              onClick: (e: MouseEvent) => {
                e.stopPropagation();
                contextMenuNodeId.value = option.key as string;
                openEntryDialog(entryPath);
              },
            },
            { icon: () => h("span", { class: "i-carbon-link" }) },
          ),
        default: () => (entryPath ? "修改入口路径" : "设置入口路径"),
      },
    ),
    h(
      NTooltip,
      { placement: "top" },
      {
        trigger: () =>
          h(
            NButton,
            {
              size: "tiny",
              quaternary: true,
              circle: true,
              onClick: (e: MouseEvent) => {
                e.stopPropagation();
                contextMenuNodeId.value = option.key as string;
                openAddChildDialog();
              },
            },
            { icon: () => h("span", { class: "i-carbon-add-alt" }) },
          ),
        default: () => "新建子模块",
      },
    ),
    h(
      NTooltip,
      { placement: "top" },
      {
        trigger: () =>
          h(
            NButton,
            {
              size: "tiny",
              quaternary: true,
              circle: true,
              onClick: (e: MouseEvent) => {
                e.stopPropagation();
                contextMenuNodeId.value = option.key as string;
                openRenameDialog(String(option.label).split("（")[0]);
              },
            },
            { icon: () => h("span", { class: "i-carbon-edit" }) },
          ),
        default: () => "重命名",
      },
    ),
    h(
      NTooltip,
      { placement: "top" },
      {
        trigger: () =>
          h(
            NButton,
            {
              size: "tiny",
              quaternary: true,
              circle: true,
              type: "error",
              onClick: (e: MouseEvent) => {
                e.stopPropagation();
                confirmDeleteModule(option.key as string, String(option.label).split("（")[0]);
              },
            },
            { icon: () => h("span", { class: "i-carbon-trash-can" }) },
          ),
        default: () => "删除",
      },
    ),
  ]);
}

function confirmDeleteModule(id: string, name: string) {
  dialog.warning({
    title: "确认删除",
    content: `确定删除模块「${name}」及其所有子模块和用例？此操作不可恢复。`,
    positiveText: "删除",
    negativeText: "取消",
    onPositiveClick: () => handleDelete(id),
  });
}

// ── Context menu ──

const showContextMenu = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuNodeId = ref<string | null>(null);

const contextMenuOptions = [
  { label: "设置入口路径", key: "set-entry" },
  { label: "新建子模块", key: "add-child" },
  { label: "重命名", key: "rename" },
  { type: "divider", key: "d1" },
  { label: "删除", key: "delete" },
];

function nodeProps({ option }: { option: TreeOption }) {
  return {
    onContextmenu(e: MouseEvent) {
      e.preventDefault();
      contextMenuNodeId.value = option.key as string;
      contextMenuX.value = e.clientX;
      contextMenuY.value = e.clientY;
      showContextMenu.value = true;
    },
  };
}

function handleContextAction(key: string) {
  showContextMenu.value = false;
  if (key === "add-child") {
    openAddChildDialog();
  } else if (key === "rename") {
    const node = findNode(modules.value, contextMenuNodeId.value!);
    openRenameDialog(node?.name ?? "");
  } else if (key === "delete") {
    const node = findNode(modules.value, contextMenuNodeId.value!);
    confirmDeleteModule(contextMenuNodeId.value!, node?.name ?? "");
  } else if (key === "set-entry") {
    const node = findNode(modules.value, contextMenuNodeId.value!);
    openEntryDialog(node?.entry_path ?? null);
  }
}

// ── Name dialog ──

const showNameDialog = ref(false);
const nameInput = ref("");
const nameDialogMode = ref<"add-root" | "add-child" | "rename">("add-root");

const nameDialogTitle = computed(() => {
  if (nameDialogMode.value === "rename") return "重命名模块";
  if (nameDialogMode.value === "add-child") return "新建子模块";
  return "新建顶级模块";
});

function handleAddRoot() {
  nameDialogMode.value = "add-root";
  nameInput.value = "";
  showNameDialog.value = true;
}

function openAddChildDialog() {
  nameDialogMode.value = "add-child";
  nameInput.value = "";
  showNameDialog.value = true;
}

function openRenameDialog(currentName: string) {
  nameDialogMode.value = "rename";
  nameInput.value = currentName;
  showNameDialog.value = true;
}

// ── Entry path dialog ──

const showEntryDialog = ref(false);
const entryInput = ref("");
// 记一份初始值，用于决定"清空入口"按钮是否显示（=只在已有 entry 时露出）
const entryInputOriginal = ref<string | null>(null);

function openEntryDialog(currentPath: string | null) {
  entryInput.value = currentPath ?? "";
  entryInputOriginal.value = currentPath;
  showEntryDialog.value = true;
}

async function confirmEntryDialog() {
  const moduleId = contextMenuNodeId.value;
  if (!moduleId) {
    showEntryDialog.value = false;
    return;
  }
  // 显式 trim：用户敲了空格也应该当作"清空"
  const cleaned = entryInput.value.trim();
  try {
    await updateModuleApi(moduleId, {
      entry_path: cleaned ? cleaned : null,
    });
    message.success(cleaned ? "入口路径已保存" : "入口路径已清空");
    showEntryDialog.value = false;
    await fetchModules();
  } catch {
    message.error("保存入口路径失败");
  }
}

async function clearEntryDialog() {
  const moduleId = contextMenuNodeId.value;
  if (!moduleId) {
    showEntryDialog.value = false;
    return;
  }
  try {
    await updateModuleApi(moduleId, { entry_path: null });
    message.success("入口路径已清空");
    showEntryDialog.value = false;
    await fetchModules();
  } catch {
    message.error("清空入口路径失败");
  }
}

async function confirmNameDialog() {
  const name = nameInput.value.trim();
  if (!name) return;

  const projectId = projectStore.currentProjectId;
  if (!projectId) return;

  try {
    if (nameDialogMode.value === "add-root") {
      await createModuleApi(projectId, { name });
      message.success("模块创建成功");
    } else if (nameDialogMode.value === "add-child") {
      await createModuleApi(projectId, {
        name,
        parent_id: contextMenuNodeId.value,
      });
      message.success("子模块创建成功");
    } else if (nameDialogMode.value === "rename") {
      await updateModuleApi(contextMenuNodeId.value!, { name });
      message.success("重命名成功");
    }
    showNameDialog.value = false;
    await fetchModules();
  } catch {
    message.error("操作失败");
  }
}

async function handleDelete(moduleId: string) {
  try {
    await deleteModuleApi(moduleId);
    message.success("模块已删除");
    if (selectedModuleId.value === moduleId) {
      selectedModuleId.value = null;
      emit("select", null);
    }
    await fetchModules();
  } catch {
    message.error("删除失败");
  }
}

// ── Selection ──

function handleSelect(keys: Array<string | number>) {
  if (keys.length === 0) {
    selectedModuleId.value = null;
    emit("select", null);
  } else {
    selectedModuleId.value = keys[0] as string;
    emit("select", keys[0] as string);
  }
}

function clearSelection() {
  selectedModuleId.value = null;
  emit("select", null);
}

// ── Data fetch ──

async function fetchModules() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) {
    modules.value = [];
    return;
  }

  loading.value = true;
  try {
    const res = await getModuleTreeApi(projectId);
    if (res.success) {
      modules.value = res.data;
    }
  } catch {
    message.error("获取模块树失败");
  } finally {
    loading.value = false;
  }
}

function findNode(
  nodes: ModuleTreeNode[],
  id: string,
): ModuleTreeNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const found = findNode(node.children, id);
    if (found) return found;
  }
  return null;
}

watch(() => projectStore.currentProjectId, fetchModules, { immediate: true });

defineExpose({
  fetchModules,
  totalCaseCount,
  clearSelection,
  openAddRootDialog: handleAddRoot,
});
</script>

<style scoped>
.module-tree {
  min-height: 200px;
}

.module-tree__empty {
  text-align: center;
  padding: 40px 12px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.module-tree__empty-text {
  margin-bottom: 8px;
}

.module-tree :deep(.module-tree__suffix) {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
}

/* 仅在悬浮时露出操作按钮组；entry-badge 始终常驻显示 */
.module-tree :deep(.module-tree__suffix > .n-button),
.module-tree :deep(.module-tree__suffix > .n-tooltip:has(.n-button)) {
  display: none;
}

.module-tree :deep(.n-tree-node:hover .module-tree__suffix > .n-button),
.module-tree :deep(.n-tree-node:hover .module-tree__suffix > .n-tooltip) {
  display: inline-flex;
}

.module-tree__entry-badge {
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  color: var(--brand-primary);
  cursor: pointer;
  padding: 0 4px;
  opacity: 0.85;
}

.module-tree__entry-badge:hover {
  opacity: 1;
}

.module-tree__entry-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.module-tree__entry-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.6;
}

.module-tree__entry-hint code {
  background: var(--bg-tertiary);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11.5px;
}

.module-tree :deep(.n-tree-node-wrapper) {
  border-radius: 6px;
}

.module-tree :deep(.n-tree-node--selected .n-tree-node-content) {
  background-color: var(--brand-primary-soft);
  color: var(--brand-primary);
}
</style>
