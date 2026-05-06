<template>
  <div>
    <page-header title="提示词管理" subtitle="按场景维护提示词模板，支持版本管理与默认绑定" icon="i-carbon-text-creation">
      <template #extra>
        <n-button type="primary" @click="handleCreate">
          <template #icon><span class="i-carbon-add" /></template>
          新建提示词
        </n-button>
      </template>
    </page-header>

    <div class="prompt-grid">
      <aside class="prompt-grid__side">
        <div class="prompt-grid__side-title">分类</div>
        <n-menu
          :value="activeCategory"
          :options="categoryMenu"
          :indent="14"
          @update:value="handleCategoryChange"
        />
      </aside>

      <page-container surface class="prompt-grid__main">
        <n-spin :show="loading">
          <n-data-table
            v-if="prompts.length > 0"
            :columns="columns"
            :data="prompts"
            :row-key="(row: PromptListItem) => row.id"
            :bordered="false"
            size="medium"
          />
          <app-empty
            v-else-if="!loading"
            icon="i-carbon-text-creation"
            title="该分类下还没有提示词"
            description="选择分类后，点击右上角「新建提示词」开始创建"
          />
        </n-spin>
      </page-container>
    </div>

    <prompt-editor
      v-model:show="editorVisible"
      :project-id="projectId"
      :prompt="editingPrompt"
      @saved="fetchPrompts"
    />

    <prompt-version-history
      v-model:show="historyVisible"
      :prompt-id="historyPromptId"
      :current-version="historyCurrentVersion"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, h, onMounted, watch } from "vue";
import {
  NButton,
  NDataTable,
  NMenu,
  NSpin,
  NTag,
  NSpace,
  NPopconfirm,
  useMessage,
} from "naive-ui";
import type { DataTableColumn, MenuOption } from "naive-ui";
import { useProjectStore } from "@/stores/project";
import {
  getPromptsApi,
  getPromptDetailApi,
  deletePromptApi,
  setPromptDefaultApi,
} from "@/services/prompts";
import type { PromptListItem, PromptInfo } from "@/services/prompts";
import PromptEditor from "@/components/prompts/PromptEditor.vue";
import PromptVersionHistory from "@/components/prompts/PromptVersionHistory.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import { PROMPT_CATEGORY_LABEL, subCategoryLabel } from "@/constants/prompts";

const projectStore = useProjectStore();
const message = useMessage();

const projectId = computed(() => projectStore.currentProjectId || "");
const loading = ref(false);
const prompts = ref<PromptListItem[]>([]);
const activeCategory = ref<string>("all");

const editorVisible = ref(false);
const editingPrompt = ref<PromptInfo | null>(null);

const historyVisible = ref(false);
const historyPromptId = ref<string | null>(null);
const historyCurrentVersion = ref(1);

const categoryMenu: MenuOption[] = [
  { label: "全部", key: "all", icon: () => h("span", { class: "i-carbon-list-boxes" }) },
  { label: "对话", key: "chat", icon: () => h("span", { class: "i-carbon-chat" }) },
  { label: "评审", key: "review", icon: () => h("span", { class: "i-carbon-analytics" }) },
  { label: "生成", key: "generation", icon: () => h("span", { class: "i-carbon-magic-wand" }) },
  { label: "UI 测试", key: "ui_test", icon: () => h("span", { class: "i-carbon-screen" }) },
  { label: "自定义", key: "custom", icon: () => h("span", { class: "i-carbon-customer" }) },
];

const columns = computed<DataTableColumn<PromptListItem>[]>(() => [
  {
    title: "名称",
    key: "name",
    ellipsis: { tooltip: true },
    render(row) {
      const parts = [];
      if (row.is_system) {
        parts.push(h(NTag, { size: "tiny", type: "warning", bordered: false }, () => "内置"));
      }
      parts.push(h("span", { class: "ml-1 font-medium" }, row.name));
      return h("div", { class: "flex items-center gap-1" }, parts);
    },
  },
  {
    title: "分类",
    key: "category",
    width: 160,
    render(row) {
      const cat = PROMPT_CATEGORY_LABEL[row.category] || row.category;
      const sub = subCategoryLabel(row.sub_category);
      const label = sub ? `${cat} / ${sub}` : cat;
      return h(NTag, { size: "small", bordered: false }, () => label);
    },
  },
  {
    title: "自动调用",
    key: "auto_apply",
    width: 90,
    render(row) {
      return row.auto_apply
        ? h(NTag, { size: "tiny", type: "success", bordered: false }, () => "是")
        : h("span", { class: "text-muted" }, "否");
    },
  },
  {
    title: "默认",
    key: "is_default",
    width: 70,
    render(row) {
      return row.is_default
        ? h(NTag, { size: "tiny", type: "info", bordered: false }, () => "默认")
        : h("span", { class: "text-muted" }, "—");
    },
  },
  {
    title: "版本",
    key: "version",
    width: 70,
    render(row) {
      return h("span", { class: "font-mono text-xs" }, `v${row.version}`);
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 240,
    render(row) {
      return h(NSpace, { size: "small" }, () => [
        h(NButton, { size: "tiny", quaternary: true, onClick: () => handleEdit(row.id) }, () => "编辑"),
        h(
          NButton,
          { size: "tiny", quaternary: true, onClick: () => handleHistory(row.id, row.version) },
          () => "历史",
        ),
        !row.is_default
          ? h(
              NButton,
              {
                size: "tiny",
                quaternary: true,
                type: "info",
                onClick: () => handleSetDefault(row.id),
              },
              () => "设默认",
            )
          : null,
        !row.is_system
          ? h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(row.id) },
              {
                trigger: () =>
                  h(NButton, { size: "tiny", quaternary: true, type: "error" }, () => "删除"),
                default: () => "确认删除此提示词？",
              },
            )
          : null,
      ]);
    },
  },
]);

function handleCategoryChange(key: string) {
  activeCategory.value = key;
  fetchPrompts();
}

async function fetchPrompts() {
  if (!projectId.value) return;
  loading.value = true;
  try {
    const category = activeCategory.value === "all" ? undefined : activeCategory.value;
    const res = await getPromptsApi(projectId.value, category);
    if (res.success) prompts.value = res.data;
  } catch {
    message.error("获取提示词列表失败");
  } finally {
    loading.value = false;
  }
}

function handleCreate() {
  editingPrompt.value = null;
  editorVisible.value = true;
}

async function handleEdit(promptId: string) {
  try {
    const res = await getPromptDetailApi(promptId);
    if (res.success) {
      editingPrompt.value = res.data;
      editorVisible.value = true;
    }
  } catch {
    message.error("获取提示词详情失败");
  }
}

function handleHistory(promptId: string, version: number) {
  historyPromptId.value = promptId;
  historyCurrentVersion.value = version;
  historyVisible.value = true;
}

async function handleSetDefault(promptId: string) {
  try {
    const res = await setPromptDefaultApi(promptId);
    if (res.success) {
      message.success("已设为默认");
      fetchPrompts();
    }
  } catch {
    message.error("操作失败");
  }
}

async function handleDelete(promptId: string) {
  try {
    const res = await deletePromptApi(promptId);
    if (res.success) {
      message.success("已删除");
      fetchPrompts();
    }
  } catch {
    message.error("删除失败");
  }
}

watch(() => projectId.value, fetchPrompts);
onMounted(fetchPrompts);
</script>

<style scoped>
.prompt-grid {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 16px;
}

.prompt-grid__side {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 8px 8px 12px;
  height: fit-content;
}

.prompt-grid__side-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  padding: 8px 12px 6px;
}

.prompt-grid__main {
  min-width: 0;
}

@media (max-width: 768px) {
  .prompt-grid {
    grid-template-columns: 1fr;
  }
}
</style>
