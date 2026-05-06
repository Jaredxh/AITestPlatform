<template>
  <div>
    <page-header title="用户管理" subtitle="管理平台账户、密码与角色权限" icon="i-carbon-user-multiple">
      <template #extra>
        <n-input
          v-model:value="searchText"
          placeholder="搜索用户名 / 邮箱 / 昵称"
          clearable
          class="w-64"
          @update:value="debouncedSearch"
        >
          <template #prefix>
            <span class="i-carbon-search text-gray-400" />
          </template>
        </n-input>
        <n-button type="primary" @click="handleCreate">
          <template #icon><span class="i-carbon-add" /></template>
          新建用户
        </n-button>
      </template>
    </page-header>

    <page-container surface>
      <n-data-table
        :columns="columns"
        :data="users"
        :loading="loading"
        :pagination="pagination"
        :row-key="(row: UserInfo) => row.id"
        :bordered="false"
        remote
        @update:page="handlePageChange"
      >
        <template #empty>
          <app-empty
            :icon="searchText ? 'i-carbon-search' : 'i-carbon-user-multiple'"
            :title="searchText ? '没有匹配的用户' : '还没有用户'"
            :description="
              searchText
                ? `没有找到包含「${searchText}」的用户，可换个关键词试试。`
                : '点击右上角「新建用户」开始创建账号。'
            "
            class="py-8"
          />
        </template>
      </n-data-table>
    </page-container>

    <user-edit-dialog
      v-model:show="editorVisible"
      :user="editingUser"
      @saved="fetchUsers"
    />

    <n-modal v-model:show="passwordModalVisible" preset="dialog" title="重置密码" :show-icon="false">
      <n-form
        ref="passwordFormRef"
        :model="passwordForm"
        :rules="passwordRules"
        label-placement="left"
        label-width="80"
        class="mt-3"
      >
        <n-form-item label="新密码" path="password">
          <n-input
            v-model:value="passwordForm.password"
            type="password"
            show-password-on="click"
            placeholder="6-128 位"
          />
        </n-form-item>
      </n-form>
      <template #action>
        <n-button @click="passwordModalVisible = false">取消</n-button>
        <n-button type="primary" :loading="resetting" @click="handleResetPassword">确认重置</n-button>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, h } from "vue";
import {
  NDataTable,
  NButton,
  NInput,
  NModal,
  NForm,
  NFormItem,
  NTag,
  NSpace,
  NTooltip,
  NPopconfirm,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, FormInst, FormRules } from "naive-ui";
import type { UserInfo } from "@/services/auth";
import {
  getUsersApi,
  deleteUserApi,
  updateUserApi,
} from "@/services/users";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import UserEditDialog from "@/components/settings/UserEditDialog.vue";

const message = useMessage();
const loading = ref(false);
const users = ref<UserInfo[]>([]);
const searchText = ref("");
const total = ref(0);
const currentPage = ref(1);
const pageSize = 20;

const editorVisible = ref(false);
const editingUser = ref<UserInfo | null>(null);

const passwordModalVisible = ref(false);
const passwordTargetUser = ref<UserInfo | null>(null);
const resetting = ref(false);
const passwordFormRef = ref<FormInst | null>(null);
const passwordForm = reactive({ password: "" });
const passwordRules: FormRules = {
  password: [
    { required: true, message: "请输入新密码", trigger: "blur" },
    { min: 6, max: 128, message: "6-128 位", trigger: "blur" },
  ],
};

const pagination = reactive({
  page: 1,
  pageSize,
  itemCount: 0,
  showSizePicker: false,
});

const columns: DataTableColumns<UserInfo> = [
  {
    title: "用户",
    key: "username",
    width: 220,
    render(row) {
      return h(
        "div",
        { class: "flex items-center gap-2" },
        [
          h(
            "div",
            { class: "user-avatar" },
            (row.display_name || row.username).charAt(0).toUpperCase(),
          ),
          h(
            "div",
            { class: "flex flex-col leading-tight min-w-0" },
            [
              h(
                "div",
                { class: "flex items-center gap-2" },
                [
                  h("span", { class: "font-medium" }, row.display_name || row.username),
                  row.is_superuser
                    ? h(NTag, { size: "tiny", type: "warning", bordered: false }, () => "超管")
                    : null,
                ],
              ),
              h("span", { class: "text-xs text-muted font-mono" }, row.username),
            ],
          ),
        ],
      );
    },
  },
  { title: "邮箱", key: "email", width: 220 },
  {
    title: "角色",
    key: "roles",
    minWidth: 220,
    render(row) {
      if (!row.roles.length)
        return h(NTag, { size: "small", type: "default", bordered: false }, () => "无角色");
      return h(
        NSpace,
        { size: 4, style: { flexWrap: "wrap" } },
        () =>
          row.roles.map((r) =>
            h(NTag, { size: "small", type: "info", bordered: false }, () => r.display_name),
          ),
      );
    },
  },
  {
    title: "状态",
    key: "is_active",
    width: 80,
    align: "center",
    render(row) {
      return h(
        NTag,
        { type: row.is_active ? "success" : "error", size: "small", bordered: false, round: true },
        () => (row.is_active ? "启用" : "禁用"),
      );
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 240,
    align: "right",
    render(row) {
      return h(NSpace, { size: 0, justify: "end" }, () => [
        h(
          NButton,
          { size: "small", quaternary: true, onClick: () => openEdit(row) },
          { default: () => "编辑", icon: () => h("span", { class: "i-carbon-edit" }) },
        ),
        h(
          NButton,
          { size: "small", quaternary: true, type: "warning", onClick: () => openPasswordReset(row) },
          { default: () => "改密", icon: () => h("span", { class: "i-carbon-password" }) },
        ),
        row.is_superuser
          ? h(
              NTooltip,
              {},
              {
                trigger: () =>
                  h(
                    NButton,
                    { size: "small", quaternary: true, type: "error", disabled: true },
                    { default: () => "删除", icon: () => h("span", { class: "i-carbon-trash-can" }) },
                  ),
                default: () => "超级管理员不可删除",
              },
            )
          : h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(row.id) },
              {
                trigger: () =>
                  h(
                    NButton,
                    { size: "small", quaternary: true, type: "error" },
                    { default: () => "删除", icon: () => h("span", { class: "i-carbon-trash-can" }) },
                  ),
                default: () => `确认删除用户 ${row.username}？`,
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
    fetchUsers();
  }, 300);
}

async function fetchUsers() {
  loading.value = true;
  try {
    const res = await getUsersApi({
      page: currentPage.value,
      page_size: pageSize,
      search: searchText.value || undefined,
    });
    if (res.success) {
      users.value = res.data.items;
      total.value = res.data.total;
      pagination.page = res.data.page;
      pagination.itemCount = res.data.total;
    }
  } catch {
    message.error("获取用户列表失败");
  } finally {
    loading.value = false;
  }
}

function handlePageChange(page: number) {
  currentPage.value = page;
  fetchUsers();
}

function handleCreate() {
  editingUser.value = null;
  editorVisible.value = true;
}

function openEdit(user: UserInfo) {
  editingUser.value = user;
  editorVisible.value = true;
}

function openPasswordReset(user: UserInfo) {
  passwordTargetUser.value = user;
  passwordForm.password = "";
  passwordModalVisible.value = true;
}

async function handleResetPassword() {
  if (!passwordTargetUser.value) return;
  try {
    await passwordFormRef.value?.validate();
  } catch {
    return;
  }
  resetting.value = true;
  try {
    const res = await updateUserApi(passwordTargetUser.value.id, {
      password: passwordForm.password,
    });
    if (res.success) {
      message.success("密码已重置");
      passwordModalVisible.value = false;
    }
  } catch {
    message.error("重置失败");
  } finally {
    resetting.value = false;
  }
}

async function handleDelete(userId: string) {
  try {
    const res = await deleteUserApi(userId);
    if (res.success) {
      message.success("用户已删除");
      fetchUsers();
    }
  } catch {
    message.error("删除失败");
  }
}

onMounted(fetchUsers);
</script>

<style scoped>
:deep(.user-avatar) {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--brand-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}
</style>
