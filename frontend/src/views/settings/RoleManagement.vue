<template>
  <div>
    <page-header title="角色管理" subtitle="维护系统角色及权限分配；可基于现有菜单按需自定义角色" icon="i-carbon-user-role">
      <template #extra>
        <n-button type="primary" @click="handleCreate">
          <template #icon><span class="i-carbon-add" /></template>
          新建角色
        </n-button>
      </template>
    </page-header>

    <page-container surface>
      <n-data-table
        :columns="columns"
        :data="roles"
        :loading="loading"
        :row-key="(row: RoleInfo) => row.id"
        :bordered="false"
        size="medium"
      />
    </page-container>

    <role-edit-dialog v-model:show="editorVisible" :role="editingRole" @saved="fetchRoles" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from "vue";
import { NButton, NDataTable, NPopconfirm, NSpace, NTag, NTooltip, useMessage } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { RoleInfo } from "@/services/auth";
import { getRolesApi, deleteRoleApi } from "@/services/users";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import RoleEditDialog from "@/components/settings/RoleEditDialog.vue";
import { summarizePermissions } from "@/constants/permissions";

const message = useMessage();
const loading = ref(false);
const roles = ref<RoleInfo[]>([]);

const editorVisible = ref(false);
const editingRole = ref<RoleInfo | null>(null);

const columns: DataTableColumns<RoleInfo> = [
  {
    title: "角色",
    key: "name",
    width: 220,
    render(row) {
      return h("div", { class: "flex flex-col gap-1" }, [
        h(
          "div",
          { class: "flex items-center gap-2" },
          [
            h("span", { class: "font-medium" }, row.display_name),
            h(
              NTag,
              { size: "tiny", bordered: false, type: row.is_system ? "warning" : "default" },
              () => (row.is_system ? "系统" : "自定义"),
            ),
          ],
        ),
        h("span", { class: "text-xs text-muted font-mono" }, row.name),
      ]);
    },
  },
  {
    title: "描述",
    key: "description",
    minWidth: 220,
    render(row) {
      return row.description
        ? h("span", { class: "text-sm" }, row.description)
        : h("span", { class: "text-muted text-sm" }, "—");
    },
  },
  {
    title: "权限范围",
    key: "permissions",
    minWidth: 320,
    render(row) {
      const groups = summarizePermissions(row.permissions);
      if (groups.length === 0) {
        return h("span", { class: "text-muted text-sm" }, "无权限");
      }
      return h(
        NSpace,
        { size: 4, style: { flexWrap: "wrap" } },
        () =>
          groups.map((g) =>
            h(
              NTooltip,
              {},
              {
                trigger: () =>
                  h(
                    NTag,
                    {
                      size: "small",
                      bordered: false,
                      type: g.full ? "info" : "default",
                    },
                    () => `${g.menu}${g.full ? "" : ` · ${g.count}/${g.total}`}`,
                  ),
                default: () =>
                  g.full
                    ? `${g.menu}：拥有全部 ${g.total} 项权限`
                    : `${g.menu}：${g.count}/${g.total} 项权限`,
              },
            ),
          ),
      );
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 200,
    align: "right",
    render(row) {
      return h(NSpace, { size: 4, justify: "end" }, () => [
        h(
          NButton,
          { size: "small", quaternary: true, onClick: () => handleEdit(row) },
          { default: () => "编辑权限", icon: () => h("span", { class: "i-carbon-edit" }) },
        ),
        row.is_system
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
                default: () => "系统内置角色不可删除",
              },
            )
          : h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(row) },
              {
                trigger: () =>
                  h(
                    NButton,
                    { size: "small", quaternary: true, type: "error" },
                    { default: () => "删除", icon: () => h("span", { class: "i-carbon-trash-can" }) },
                  ),
                default: () => `确认删除角色「${row.display_name}」？该角色下所有用户将失去对应权限。`,
              },
            ),
      ]);
    },
  },
];

async function fetchRoles() {
  loading.value = true;
  try {
    const res = await getRolesApi();
    if (res.success) roles.value = res.data;
  } catch {
    message.error("获取角色列表失败");
  } finally {
    loading.value = false;
  }
}

function handleCreate() {
  editingRole.value = null;
  editorVisible.value = true;
}

function handleEdit(role: RoleInfo) {
  editingRole.value = role;
  editorVisible.value = true;
}

async function handleDelete(role: RoleInfo) {
  try {
    const res = await deleteRoleApi(role.id);
    if (res.success) {
      message.success("角色已删除");
      fetchRoles();
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "删除失败";
    message.error(msg);
  }
}

onMounted(fetchRoles);
</script>
