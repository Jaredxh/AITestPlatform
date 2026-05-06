<template>
  <div>
    <page-header title="项目管理" subtitle="为不同业务线建立独立的需求 / 用例 / 配置空间" icon="i-carbon-folder">
      <template #extra>
        <n-input
          v-model:value="searchText"
          placeholder="搜索项目名称"
          clearable
          class="w-56"
          @update:value="debouncedSearch"
        >
          <template #prefix>
            <span class="i-carbon-search text-gray-400" />
          </template>
        </n-input>
        <n-button type="primary" @click="openCreateModal">
          <template #icon>
            <span class="i-carbon-add" />
          </template>
          创建项目
        </n-button>
      </template>
    </page-header>

    <n-spin :show="loading">
      <n-grid v-if="projects.length > 0" :cols="3" :x-gap="16" :y-gap="16" responsive="screen" :item-responsive="true">
        <n-gi v-for="project in projects" :key="project.id" span="0:3 640:1">
          <div class="project-card card-hover" @click="handleSelect(project)">
            <div class="project-card__header">
              <div class="project-card__icon">
                <span class="i-carbon-folder" />
              </div>
              <div class="project-card__title-block">
                <div class="project-card__title">
                  <n-ellipsis :tooltip="false">{{ project.name }}</n-ellipsis>
                </div>
                <span
                  class="project-card__status-tag"
                  :class="`project-card__status-tag--${project.status}`"
                >
                  <span class="project-card__status-dot" />
                  {{ project.status === "active" ? "活跃" : "已归档" }}
                </span>
              </div>
            </div>

            <n-ellipsis :line-clamp="2" class="project-card__desc">
              {{ project.description || "暂无项目描述" }}
            </n-ellipsis>

            <div class="project-card__meta">
              <div class="project-card__meta-item">
                <span class="i-carbon-user-multiple" />
                <span>{{ project.member_count }} 名成员</span>
              </div>
              <div class="project-card__meta-item">
                <span class="i-carbon-time" />
                <span>{{ formatDate(project.created_at) }}</span>
              </div>
            </div>

            <div class="project-card__footer" @click.stop>
              <n-button size="small" quaternary @click="handleSettings(project)">
                <template #icon><span class="i-carbon-settings" /></template>
                设置
              </n-button>
              <n-popconfirm @positive-click="handleDelete(project.id)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">
                    <template #icon><span class="i-carbon-trash-can" /></template>
                    删除
                  </n-button>
                </template>
                确认删除项目「{{ project.name }}」？此操作不可恢复。
              </n-popconfirm>
            </div>
          </div>
        </n-gi>
      </n-grid>

      <app-empty
        v-else-if="!loading && searchText"
        icon="i-carbon-search"
        title="没有匹配的项目"
        :description="`没有找到包含「${searchText}」的项目，可换个关键词试试。`"
      >
        <template #actions>
          <n-button @click="clearSearch">清空搜索</n-button>
        </template>
      </app-empty>
      <app-empty
        v-else-if="!loading"
        icon="i-carbon-folder-add"
        title="还没有项目"
        description="项目用于区分不同的产品线 / 业务模块，每个项目独立管理需求与用例"
      >
        <template #actions>
          <n-button type="primary" @click="openCreateModal">
            <template #icon><span class="i-carbon-add" /></template>
            创建第一个项目
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

    <n-modal
      v-model:show="formVisible"
      preset="dialog"
      :title="editingProject ? '编辑项目' : '创建项目'"
      :show-icon="false"
      style="width: 480px"
    >
      <n-form
        ref="formRef"
        :model="formData"
        :rules="formRules"
        label-placement="left"
        label-width="70"
        class="mt-4"
      >
        <n-form-item label="项目名称" path="name">
          <n-input v-model:value="formData.name" placeholder="输入项目名称" maxlength="100" show-count />
        </n-form-item>
        <n-form-item label="项目描述" path="description">
          <n-input
            v-model:value="formData.description"
            type="textarea"
            placeholder="简要描述项目用途（可选）"
            :rows="3"
          />
        </n-form-item>
        <n-form-item v-if="editingProject" label="状态" path="status">
          <n-select
            v-model:value="formData.status"
            :options="statusOptions"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="formVisible = false">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="handleSubmit">
          {{ editingProject ? "保存" : "创建" }}
        </n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { useRouter } from "vue-router";
import {
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
  NSelect,
  NSpin,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";
import {
  getProjectsApi,
  createProjectApi,
  updateProjectApi,
  deleteProjectApi,
} from "@/services/projects";
import type { ProjectInfo } from "@/services/projects";
import { useProjectStore } from "@/stores/project";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";

const router = useRouter();
const message = useMessage();
const projectStore = useProjectStore();

const loading = ref(false);
const submitting = ref(false);
const projects = ref<ProjectInfo[]>([]);
const searchText = ref("");
const total = ref(0);
const currentPage = ref(1);
const pageSize = 12;

const formVisible = ref(false);
const formRef = ref<FormInst | null>(null);
const editingProject = ref<ProjectInfo | null>(null);
const formData = reactive({
  name: "",
  description: "",
  status: "active" as string,
});

const formRules: FormRules = {
  name: [{ required: true, message: "请输入项目名称", trigger: "blur" }],
};

const statusOptions = [
  { label: "活跃", value: "active" },
  { label: "已归档", value: "archived" },
];

let searchTimer: ReturnType<typeof setTimeout>;
function debouncedSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentPage.value = 1;
    fetchProjects();
  }, 300);
}

async function fetchProjects() {
  loading.value = true;
  try {
    const res = await getProjectsApi({
      page: currentPage.value,
      page_size: pageSize,
      search: searchText.value || undefined,
    });
    if (res.success) {
      projects.value = res.data.items;
      total.value = res.data.total;
    }
  } catch {
    message.error("获取项目列表失败");
  } finally {
    loading.value = false;
  }
}

function handlePageChange(page: number) {
  currentPage.value = page;
  fetchProjects();
}

function clearSearch() {
  searchText.value = "";
  currentPage.value = 1;
  fetchProjects();
}

function openCreateModal() {
  editingProject.value = null;
  formData.name = "";
  formData.description = "";
  formData.status = "active";
  formVisible.value = true;
}

function handleSettings(project: ProjectInfo) {
  router.push({ name: "ProjectSettings", params: { projectId: project.id } });
}

function handleSelect(project: ProjectInfo) {
  projectStore.setCurrentProject(project.id);
  message.success(`已切换到项目「${project.name}」`);
  router.push({ name: "Dashboard" });
}

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  submitting.value = true;
  try {
    if (editingProject.value) {
      const res = await updateProjectApi(editingProject.value.id, {
        name: formData.name,
        description: formData.description || undefined,
        status: formData.status as "active" | "archived",
      });
      if (res.success) {
        message.success("项目更新成功");
        projectStore.addProjectToList(res.data);
        formVisible.value = false;
        fetchProjects();
      }
    } else {
      const res = await createProjectApi({
        name: formData.name,
        description: formData.description || undefined,
      });
      if (res.success) {
        message.success("项目创建成功");
        projectStore.addProjectToList(res.data);
        projectStore.setCurrentProject(res.data.id);
        formVisible.value = false;
        fetchProjects();
      }
    }
  } catch {
    message.error(editingProject.value ? "更新失败" : "创建失败");
  } finally {
    submitting.value = false;
  }
}

async function handleDelete(projectId: string) {
  try {
    const res = await deleteProjectApi(projectId);
    if (res.success) {
      message.success("项目已删除");
      projectStore.removeProjectFromList(projectId);
      fetchProjects();
    }
  } catch {
    message.error("删除失败");
  }
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString("zh-CN");
}

onMounted(fetchProjects);
</script>

<style scoped>
.project-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 18px 20px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 188px;
}

.project-card__header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}

.project-card__icon {
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

.project-card__title-block {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.project-card__status-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px 2px 6px;
  font-size: 11px;
  line-height: 1.4;
  border-radius: 999px;
  font-weight: 500;
  width: fit-content;
}

.project-card__status-tag--active {
  background: rgba(24, 160, 88, 0.1);
  color: var(--color-success, #18a058);
}

.project-card__status-tag--archived {
  background: rgba(240, 160, 32, 0.12);
  color: var(--color-warning, #f0a020);
}

.project-card__status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.project-card__title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
  letter-spacing: -0.1px;
}

.project-card__desc {
  font-size: 13px;
  color: var(--text-tertiary);
  flex: 1;
  margin-bottom: 12px;
  line-height: 1.55;
}

.project-card__meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
  color: var(--text-tertiary);
  padding-top: 12px;
  border-top: 1px dashed var(--border-subtle);
}

.project-card__meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
}

.project-card__footer {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 10px;
  opacity: 0.7;
  transition: opacity var(--duration-fast) var(--easing-standard);
}

.project-card:hover .project-card__footer {
  opacity: 1;
}
</style>
