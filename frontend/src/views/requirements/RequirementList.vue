<template>
  <div>
    <page-header title="需求文档" subtitle="上传 PRD / 需求说明，结合 AI 进行评审与分析" icon="i-carbon-document">
      <template #extra>
        <n-input
          v-model:value="searchText"
          placeholder="搜索文档名称"
          clearable
          class="w-56"
          @update:value="debouncedSearch"
        >
          <template #prefix>
            <span class="i-carbon-search text-gray-400" />
          </template>
        </n-input>
        <n-button type="primary" @click="showUpload = true">
          <template #icon>
            <span class="i-carbon-upload" />
          </template>
          上传文档
        </n-button>
      </template>
    </page-header>

    <n-alert v-if="!projectStore.currentProjectId" type="warning" class="mb-4">
      请先在顶栏选择一个项目，再管理需求文档。
    </n-alert>

    <page-container surface>
      <transition name="fade-slide">
        <div v-if="checkedRowKeys.length > 0" class="batch-bar">
          <span class="batch-bar__count">
            已选 <strong>{{ checkedRowKeys.length }}</strong> 份文档
          </span>
          <div class="batch-bar__actions">
            <n-popconfirm @positive-click="handleBatchDelete">
              <template #trigger>
                <n-button size="small" type="error" ghost>
                  <template #icon><span class="i-carbon-trash-can" /></template>
                  批量删除
                </n-button>
              </template>
              确认删除选中的 {{ checkedRowKeys.length }} 份需求文档？此操作不可恢复。
            </n-popconfirm>
            <n-button size="small" quaternary @click="checkedRowKeys = []">
              取消选择
            </n-button>
          </div>
        </div>
      </transition>

      <n-spin :show="loading">
        <n-data-table
          v-if="documents.length > 0 || loading"
          v-model:checked-row-keys="checkedRowKeys"
          :columns="columns"
          :data="documents"
          :row-key="(row: DocumentInfo) => row.id"
          :bordered="false"
          striped
        />
        <app-empty
          v-else-if="!loading && searchText"
          icon="i-carbon-search"
          title="没有匹配的文档"
          :description="`没有找到包含「${searchText}」的需求文档，可换个关键词试试。`"
        >
          <template #actions>
            <n-button @click="clearSearch">清空搜索</n-button>
          </template>
        </app-empty>
        <app-empty
          v-else-if="!loading"
          icon="i-carbon-document-blank"
          title="还没有需求文档"
          description="上传 PDF / DOC / DOCX 格式的需求文档，开启 AI 评审与用例生成"
        >
          <template #actions>
            <n-button type="primary" @click="showUpload = true">
              <template #icon><span class="i-carbon-upload" /></template>
              上传第一份文档
            </n-button>
          </template>
        </app-empty>
      </n-spin>
    </page-container>

    <div v-if="total > 0" class="pager">
      <n-text depth="3" class="text-xs">共 {{ total }} 份文档</n-text>
      <n-pagination
        v-model:page="currentPage"
        :item-count="total"
        :page-size="pageSize"
        :page-sizes="pageSizeOptions"
        show-size-picker
        @update:page="handlePageChange"
        @update:page-size="handlePageSizeChange"
      />
    </div>

    <upload-dialog v-model:show="showUpload" @success="fetchDocuments" />
  </div>
</template>

<script setup lang="ts">
import { ref, h, onMounted, watch } from "vue";
import { useRouter } from "vue-router";
import {
  NButton,
  NDataTable,
  NInput,
  NPagination,
  NPopconfirm,
  NSpin,
  NTag,
  NText,
  NAlert,
  useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import {
  getDocumentsApi,
  deleteDocumentApi,
} from "@/services/requirements";
import type { DocumentInfo } from "@/services/requirements";
import { useProjectStore } from "@/stores/project";
import UploadDialog from "@/components/requirements/UploadDialog.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";

const router = useRouter();
const message = useMessage();
const projectStore = useProjectStore();

const loading = ref(false);
const documents = ref<DocumentInfo[]>([]);
const searchText = ref("");
const total = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);
const pageSizeOptions = [10, 20, 50, 100];
const showUpload = ref(false);
const checkedRowKeys = ref<string[]>([]);

const columns: DataTableColumns<DocumentInfo> = [
  { type: "selection" },
  {
    title: "文件名",
    key: "filename",
    ellipsis: { tooltip: true },
    render(row) {
      return h(
        "a",
        {
          class: "text-link cursor-pointer hover:underline",
          style: { color: "var(--brand-primary)" },
          onClick: () => handleView(row),
        },
        row.filename,
      );
    },
  },
  {
    title: "格式",
    key: "content_type",
    width: 90,
    render(row) {
      const ct = row.content_type;
      const name = row.filename.toLowerCase();
      let label = "DOCX";
      let type: "error" | "info" | "warning" = "info";
      if (ct.includes("pdf") || name.endsWith(".pdf")) {
        label = "PDF";
        type = "error";
      } else if (name.endsWith(".doc")) {
        label = "DOC";
        type = "warning";
      }
      return h(NTag, { size: "small", type, bordered: false }, () => label);
    },
  },
  {
    title: "大小",
    key: "file_size",
    width: 90,
    render(row) {
      return formatSize(row.file_size);
    },
  },
  {
    title: "解析状态",
    key: "status",
    width: 100,
    render(row) {
      const map: Record<string, { label: string; type: "success" | "warning" | "error" }> = {
        parsed: { label: "已解析", type: "success" },
        pending: { label: "待解析", type: "warning" },
        failed: { label: "解析失败", type: "error" },
      };
      const s = map[row.status] || { label: row.status, type: "warning" as const };
      return h(NTag, { size: "small", type: s.type, bordered: false }, () => s.label);
    },
  },
  {
    title: "评审状态",
    key: "review_status",
    width: 130,
    render(row) {
      const map: Record<string, { label: string; type: "success" | "warning" | "error" | "default"; icon: string }> = {
        unreviewed: { label: "未评审", type: "default", icon: "i-carbon-circle-dash" },
        reviewing: { label: "评审中", type: "warning", icon: "i-carbon-renew" },
        reviewed: { label: "已评审", type: "success", icon: "i-carbon-checkmark-outline" },
        failed: { label: "评审失败", type: "error", icon: "i-carbon-warning" },
      };
      const s = map[row.review_status] || map.unreviewed;
      const children: unknown[] = [
        h("span", { class: `${s.icon} mr-1` }),
        h("span", null, s.label),
      ];
      if (row.last_review_score != null) {
        children.push(
          h(
            "span",
            { class: "ml-1 font-medium", style: { color: scoreColor(row.last_review_score) } },
            ` · ${row.last_review_score.toFixed(0)}分`,
          ),
        );
      }
      return h(NTag, { size: "small", type: s.type, bordered: false }, () => children);
    },
  },
  {
    title: "上传者",
    key: "uploader_name",
    width: 100,
    render(row) {
      return row.uploader_name || "-";
    },
  },
  {
    title: "上传时间",
    key: "created_at",
    width: 160,
    render(row) {
      return new Date(row.created_at).toLocaleString("zh-CN");
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 160,
    render(row) {
      return h("div", { class: "flex gap-2" }, [
        h(
          NButton,
          { size: "small", quaternary: true, onClick: () => handleView(row) },
          { default: () => "查看", icon: () => h("span", { class: "i-carbon-view" }) },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(row.id) },
          {
            trigger: () =>
              h(
                NButton,
                { size: "small", type: "error", quaternary: true },
                { default: () => "删除", icon: () => h("span", { class: "i-carbon-trash-can" }) },
              ),
            default: () => `确认删除「${row.filename}」？`,
          },
        ),
      ]);
    },
  },
];

let searchTimer: ReturnType<typeof setTimeout>;
function debouncedSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentPage.value = 1;
    fetchDocuments();
  }, 300);
}

async function fetchDocuments() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;

  loading.value = true;
  try {
    const res = await getDocumentsApi(projectId, {
      page: currentPage.value,
      page_size: pageSize.value,
      search: searchText.value || undefined,
    });
    if (res.success) {
      documents.value = res.data.items;
      total.value = res.data.total;
      // 翻页/搜索后清理跨页选择
      checkedRowKeys.value = checkedRowKeys.value.filter((id) =>
        res.data.items.some((doc) => doc.id === id),
      );
    }
  } catch {
    message.error("获取文档列表失败");
  } finally {
    loading.value = false;
  }
}

function handlePageChange(page: number) {
  currentPage.value = page;
  fetchDocuments();
}

function handlePageSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
  fetchDocuments();
}

async function handleBatchDelete() {
  if (checkedRowKeys.value.length === 0) return;
  try {
    await Promise.all(checkedRowKeys.value.map((id) => deleteDocumentApi(id)));
    message.success(`已删除 ${checkedRowKeys.value.length} 份文档`);
    checkedRowKeys.value = [];
    fetchDocuments();
  } catch {
    message.error("批量删除失败");
  }
}

function clearSearch() {
  searchText.value = "";
  currentPage.value = 1;
  fetchDocuments();
}

function scoreColor(score: number): string {
  if (score >= 80) return "var(--color-success, #18a058)";
  if (score >= 60) return "var(--color-warning, #f0a020)";
  return "var(--color-error, #d03050)";
}

function handleView(doc: DocumentInfo) {
  router.push({ name: "RequirementDetail", params: { documentId: doc.id } });
}

async function handleDelete(docId: string) {
  try {
    const res = await deleteDocumentApi(docId);
    if (res.success) {
      message.success("文档已删除");
      fetchDocuments();
    }
  } catch {
    message.error("删除失败");
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

watch(() => projectStore.currentProjectId, () => {
  currentPage.value = 1;
  fetchDocuments();
});

onMounted(fetchDocuments);
</script>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 8px 14px;
  background: var(--brand-primary-soft);
  border: 1px solid var(--brand-primary-border);
  border-radius: var(--radius-md);
}

.batch-bar__count {
  font-size: 13px;
  color: var(--text-secondary);
}

.batch-bar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding: 0 4px;
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
</style>
