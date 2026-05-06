<template>
  <n-modal
    :show="show"
    preset="card"
    :title="dialogTitle"
    style="width: 560px"
    :segmented="{ content: true }"
    :mask-closable="false"
    @update:show="$emit('update:show', $event)"
  >
    <n-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-placement="left"
      label-width="78"
      class="mt-1"
    >
      <n-form-item label="用户名" path="username">
        <n-input
          v-model:value="form.username"
          placeholder="3-50 位字母 / 数字 / 下划线"
          :disabled="isEdit"
          maxlength="50"
        />
      </n-form-item>
      <n-form-item label="邮箱" path="email">
        <n-input v-model:value="form.email" placeholder="name@company.com" />
      </n-form-item>
      <n-form-item label="昵称" path="display_name">
        <n-input v-model:value="form.display_name" placeholder="显示名称（选填）" maxlength="100" />
      </n-form-item>
      <n-form-item :label="isEdit ? '重置密码' : '密码'" path="password">
        <n-input
          v-model:value="form.password"
          type="password"
          show-password-on="click"
          :placeholder="isEdit ? '留空则不修改' : '至少 6 位'"
        />
      </n-form-item>
      <n-form-item label="角色" path="role_ids">
        <n-select
          v-model:value="form.role_ids"
          :options="roleOptions"
          multiple
          placeholder="选择一个或多个角色"
          :loading="rolesLoading"
        />
      </n-form-item>
      <n-form-item label="状态">
        <n-switch v-model:value="form.is_active">
          <template #checked>启用</template>
          <template #unchecked>禁用</template>
        </n-switch>
      </n-form-item>
    </n-form>

    <template #action>
      <div class="flex justify-end gap-2">
        <n-button @click="$emit('update:show', false)">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="handleSubmit">
          {{ isEdit ? "保存" : "创建" }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import {
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSwitch,
  NButton,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";
import type { UserInfo, RoleInfo } from "@/services/auth";
import {
  createUserApi,
  updateUserApi,
  updateUserRolesApi,
  getRolesApi,
} from "@/services/users";

const props = defineProps<{
  show: boolean;
  user: UserInfo | null;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
  saved: [];
}>();

const message = useMessage();
const formRef = ref<FormInst | null>(null);
const submitting = ref(false);

const isEdit = computed(() => !!props.user);
const dialogTitle = computed(() => (isEdit.value ? `编辑用户：${props.user?.username}` : "新建用户"));

const allRoles = ref<RoleInfo[]>([]);
const rolesLoading = ref(false);

const roleOptions = computed(() =>
  allRoles.value.map((r) => ({ label: r.display_name, value: r.id })),
);

const form = reactive({
  username: "",
  email: "",
  display_name: "",
  password: "",
  is_active: true,
  role_ids: [] as string[],
});

const rules: FormRules = {
  username: [
    { required: true, message: "请输入用户名", trigger: "blur" },
    {
      validator: (_, v: string) => !v || /^[a-zA-Z0-9_]{3,50}$/.test(v),
      message: "3-50 位字母 / 数字 / 下划线",
      trigger: "blur",
    },
  ],
  email: [
    { required: true, message: "请输入邮箱", trigger: "blur" },
    { type: "email", message: "邮箱格式不正确", trigger: "blur" },
  ],
  password: [
    {
      validator: (_, v: string) => {
        if (!v) return !!props.user;
        return v.length >= 6 && v.length <= 128;
      },
      message: "密码长度需 6-128 位",
      trigger: "blur",
    },
  ],
};

async function fetchRoles() {
  rolesLoading.value = true;
  try {
    const res = await getRolesApi();
    if (res.success) allRoles.value = res.data;
  } finally {
    rolesLoading.value = false;
  }
}

watch(
  () => [props.show, props.user],
  ([show]) => {
    if (!show) return;
    if (allRoles.value.length === 0) fetchRoles();
    if (props.user) {
      form.username = props.user.username;
      form.email = props.user.email;
      form.display_name = props.user.display_name || "";
      form.password = "";
      form.is_active = props.user.is_active;
      form.role_ids = props.user.roles.map((r) => r.id);
    } else {
      form.username = "";
      form.email = "";
      form.display_name = "";
      form.password = "";
      form.is_active = true;
      form.role_ids = [];
    }
  },
);

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  submitting.value = true;
  try {
    if (isEdit.value && props.user) {
      const res = await updateUserApi(props.user.id, {
        email: form.email || undefined,
        display_name: form.display_name || undefined,
        is_active: form.is_active,
        password: form.password || undefined,
      });
      if (!res.success) throw new Error(res.message || "保存失败");
      const originalRoleIds = new Set(props.user.roles.map((r) => r.id));
      const next = new Set(form.role_ids);
      const changed =
        next.size !== originalRoleIds.size ||
        [...next].some((id) => !originalRoleIds.has(id));
      if (changed) {
        await updateUserRolesApi(props.user.id, form.role_ids);
      }
      message.success("用户已更新");
    } else {
      const res = await createUserApi({
        username: form.username,
        email: form.email,
        password: form.password,
        display_name: form.display_name || undefined,
        is_active: form.is_active,
        role_ids: form.role_ids,
      });
      if (!res.success) throw new Error(res.message || "创建失败");
      message.success("用户已创建");
    }
    emit("update:show", false);
    emit("saved");
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "";
    message.error(msg || (isEdit.value ? "保存失败" : "创建失败"));
  } finally {
    submitting.value = false;
  }
}
</script>
