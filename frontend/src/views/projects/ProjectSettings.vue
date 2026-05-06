<template>
  <div>
    <page-header
      :title="project?.name || '项目设置'"
      subtitle="管理项目基本信息与成员"
      icon="i-carbon-settings"
      back
      @back="router.push({ name: 'ProjectList' })"
    >
      <template #title-extra>
        <n-tag
          v-if="project"
          :type="project.status === 'active' ? 'success' : 'warning'"
          size="small"
          :bordered="false"
          round
        >
          {{ project.status === "active" ? "活跃" : "已归档" }}
        </n-tag>
      </template>
    </page-header>

    <n-spin :show="loading">
      <n-tabs v-if="project" type="line">
        <!-- 基本信息 -->
        <n-tab-pane name="info" tab="基本信息">
          <n-card class="max-w-xl">
            <n-form :model="infoForm" label-placement="left" label-width="80">
              <n-form-item label="项目名称">
                <n-input v-model:value="infoForm.name" maxlength="100" show-count />
              </n-form-item>
              <n-form-item label="项目描述">
                <n-input v-model:value="infoForm.description" type="textarea" :rows="3" />
              </n-form-item>
              <n-form-item label="状态">
                <n-select v-model:value="infoForm.status" :options="statusOptions" class="w-40" />
              </n-form-item>
              <n-form-item>
                <n-button type="primary" :loading="savingInfo" @click="handleSaveInfo">
                  保存修改
                </n-button>
              </n-form-item>
            </n-form>
          </n-card>
        </n-tab-pane>

        <!-- 成员管理 -->
        <n-tab-pane name="members" tab="成员管理">
          <div class="flex-between mb-4">
            <span class="text-sm text-gray-500">共 {{ project.members.length }} 名成员</span>
            <n-button type="primary" size="small" @click="addMemberVisible = true">
              <template #icon><span class="i-carbon-user-follow" /></template>
              添加成员
            </n-button>
          </div>

          <n-data-table
            :columns="memberColumns"
            :data="project.members"
            :row-key="(row: ProjectMember) => row.user_id"
          />
        </n-tab-pane>
      </n-tabs>

      <n-empty v-else-if="!loading" description="项目不存在" class="mt-20" />
    </n-spin>

    <!-- 添加成员弹窗 -->
    <n-modal
      v-model:show="addMemberVisible"
      preset="dialog"
      title="添加成员"
      :show-icon="false"
      style="width: 460px"
      @after-enter="ensureUsersLoaded"
    >
      <n-form label-placement="left" label-width="60" class="mt-4">
        <n-form-item label="用户">
          <n-select
            v-model:value="addMemberForm.userId"
            filterable
            placeholder="选择或筛选用户（可输入用户名/昵称/邮箱过滤）"
            :options="userOptions"
            :loading="loadingUsers"
            :consistent-menu-width="false"
            :menu-props="{ style: { maxHeight: '320px' } }"
          />
        </n-form-item>
        <n-form-item label="角色">
          <n-select v-model:value="addMemberForm.role" :options="roleOptions" />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="addMemberVisible = false">取消</n-button>
        <n-button
          type="primary"
          :loading="addingMember"
          :disabled="!addMemberForm.userId"
          @click="handleAddMember"
        >
          添加
        </n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, h } from "vue";
import { useRouter, useRoute } from "vue-router";
import {
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  useMessage,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import type { DataTableColumns, SelectOption } from "naive-ui";
import {
  getProjectDetailApi,
  updateProjectApi,
  addProjectMemberApi,
  removeProjectMemberApi,
} from "@/services/projects";
import type { ProjectDetail, ProjectMember } from "@/services/projects";
import { getUsersApi } from "@/services/users";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const route = useRoute();
const message = useMessage();
const projectStore = useProjectStore();
const authStore = useAuthStore();

const loading = ref(false);
const savingInfo = ref(false);
const addingMember = ref(false);
const loadingUsers = ref(false);
const project = ref<ProjectDetail | null>(null);

const infoForm = reactive({
  name: "",
  description: "",
  status: "active" as string,
});

const addMemberVisible = ref(false);
const addMemberForm = reactive({
  userId: null as string | null,
  role: "member" as string,
});
const userOptions = ref<SelectOption[]>([]);

const statusOptions = [
  { label: "活跃", value: "active" },
  { label: "已归档", value: "archived" },
];

const roleOptions = [
  { label: "管理员", value: "admin" },
  { label: "成员", value: "member" },
  { label: "观察者", value: "viewer" },
];

const roleLabelMap: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "观察者",
};

const roleTagTypeMap: Record<string, "success" | "info" | "warning" | "default"> = {
  owner: "success",
  admin: "info",
  member: "default",
  viewer: "warning",
};

const isProjectAdmin = () => {
  if (!project.value || !authStore.user) return false;
  if (authStore.user.is_superuser) return true;
  const m = project.value.members.find((m) => m.user_id === authStore.user!.id);
  return m?.role === "owner" || m?.role === "admin";
};

const memberColumns: DataTableColumns<ProjectMember> = [
  { title: "用户名", key: "username", width: 140 },
  {
    title: "昵称",
    key: "display_name",
    width: 140,
    render(row) {
      return row.display_name || "-";
    },
  },
  {
    title: "角色",
    key: "role",
    width: 120,
    render(row) {
      return h(
        NTag,
        { type: roleTagTypeMap[row.role] || "default", size: "small" },
        () => roleLabelMap[row.role] || row.role,
      );
    },
  },
  {
    title: "加入时间",
    key: "joined_at",
    width: 140,
    render(row) {
      return new Date(row.joined_at).toLocaleDateString("zh-CN");
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 100,
    render(row) {
      if (row.role === "owner") return null;
      if (!isProjectAdmin()) return null;
      return h(
        NPopconfirm,
        {
          onPositiveClick: () => handleRemoveMember(row.user_id),
        },
        {
          trigger: () => h(NButton, { size: "small", type: "error", ghost: true }, () => "移除"),
          default: () => `确认移除成员「${row.username}」？`,
        },
      );
    },
  },
];

async function fetchProject() {
  const projectId = route.params.projectId as string;
  if (!projectId) return;

  loading.value = true;
  try {
    const res = await getProjectDetailApi(projectId);
    if (res.success) {
      project.value = res.data;
      infoForm.name = res.data.name;
      infoForm.description = res.data.description || "";
      infoForm.status = res.data.status;
    }
  } catch {
    message.error("获取项目信息失败");
  } finally {
    loading.value = false;
  }
}

async function handleSaveInfo() {
  if (!project.value) return;
  savingInfo.value = true;
  try {
    const res = await updateProjectApi(project.value.id, {
      name: infoForm.name,
      description: infoForm.description || undefined,
      status: infoForm.status as "active" | "archived",
    });
    if (res.success) {
      message.success("项目信息已更新");
      projectStore.addProjectToList(res.data);
      fetchProject();
    }
  } catch {
    message.error("保存失败");
  } finally {
    savingInfo.value = false;
  }
}

async function loadAllUsers() {
  loadingUsers.value = true;
  try {
    // 一次性拉取尽量多的用户，做本地过滤
    const res = await getUsersApi({ page: 1, page_size: 100 });
    if (res.success) {
      const existingIds = new Set(project.value?.members.map((m) => m.user_id) || []);
      userOptions.value = res.data.items
        .filter((u) => !existingIds.has(u.id))
        .map((u) => ({
          label: u.display_name ? `${u.display_name}（${u.username}）` : u.username,
          value: u.id,
        }));
    }
  } finally {
    loadingUsers.value = false;
  }
}

function ensureUsersLoaded() {
  // 弹窗打开时确保用户列表已加载并刷新（排除已是成员的）
  loadAllUsers();
}

async function handleAddMember() {
  if (!project.value || !addMemberForm.userId) return;
  addingMember.value = true;
  try {
    const res = await addProjectMemberApi(project.value.id, {
      user_id: addMemberForm.userId,
      role: addMemberForm.role as "admin" | "member" | "viewer",
    });
    if (res.success) {
      message.success("成员添加成功");
      addMemberVisible.value = false;
      addMemberForm.userId = null;
      addMemberForm.role = "member";
      fetchProject();
    }
  } catch {
    message.error("添加成员失败");
  } finally {
    addingMember.value = false;
  }
}

async function handleRemoveMember(userId: string) {
  if (!project.value) return;
  try {
    const res = await removeProjectMemberApi(project.value.id, userId);
    if (res.success) {
      message.success("成员已移除");
      fetchProject();
    }
  } catch {
    message.error("移除成员失败");
  }
}

onMounted(fetchProject);
</script>
