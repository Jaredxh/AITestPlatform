<template>
  <n-modal
    :show="show"
    preset="card"
    :title="isEdit ? `编辑角色：${form.display_name || ''}` : '新建角色'"
    style="width: 720px"
    :segmented="{ content: true }"
    :mask-closable="false"
    @update:show="$emit('update:show', $event)"
  >
    <n-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-placement="left"
      label-width="86"
      class="mt-1"
    >
      <n-grid :cols="2" :x-gap="16">
        <n-gi>
          <n-form-item label="角色标识" path="name">
            <n-input
              v-model:value="form.name"
              placeholder="如 qa_lead"
              :disabled="isEdit"
              maxlength="50"
            />
          </n-form-item>
        </n-gi>
        <n-gi>
          <n-form-item label="显示名称" path="display_name">
            <n-input v-model:value="form.display_name" placeholder="如 测试主管" maxlength="100" />
          </n-form-item>
        </n-gi>
      </n-grid>

      <n-form-item label="描述" path="description">
        <n-input
          v-model:value="form.description"
          type="textarea"
          :rows="2"
          placeholder="一句话说明角色职责（选填）"
          maxlength="500"
        />
      </n-form-item>

      <n-form-item label="菜单与权限">
        <div class="role-perm">
          <div class="role-perm__hint">
            勾选 <strong>菜单</strong> 表示该角色可访问此菜单；可单独勾选 <strong>子权限</strong> 精细控制；
            不勾选任何子权限的菜单，对该角色 <strong>不可见</strong>。
          </div>
          <n-tree
            :data="treeData"
            :checked-keys="checkedKeys"
            :default-expand-all="true"
            cascade
            checkable
            block-line
            check-strategy="all"
            class="role-perm__tree"
            @update:checked-keys="onCheckedKeys"
          />
          <div class="role-perm__actions">
            <n-button size="small" quaternary @click="handleSelectAll">全选</n-button>
            <n-button size="small" quaternary @click="handleClear">清空</n-button>
            <span class="role-perm__count">
              已选 {{ pickedPermissionCount }} / {{ totalPermissionCount }} 项权限
            </span>
          </div>
        </div>
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
  NGrid,
  NGi,
  NTree,
  NButton,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules, TreeOption } from "naive-ui";
import type { RoleInfo } from "@/services/auth";
import { createRoleApi, updateRoleApi } from "@/services/users";
import { PERMISSION_GROUPS } from "@/constants/permissions";

const props = defineProps<{
  show: boolean;
  role: RoleInfo | null;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
  saved: [];
}>();

const message = useMessage();
const formRef = ref<FormInst | null>(null);
const submitting = ref(false);

const isEdit = computed(() => !!props.role);

const form = reactive({
  name: "",
  display_name: "",
  description: "",
});

const rules: FormRules = {
  name: [
    { required: true, message: "请输入角色标识", trigger: "blur" },
    {
      validator: (_, v: string) => !!v && /^[a-z][a-z0-9_]*$/.test(v),
      message: "仅支持小写字母、数字和下划线，且需以字母开头",
      trigger: "blur",
    },
  ],
  display_name: [{ required: true, message: "请输入显示名称", trigger: "blur" }],
};

const treeData = computed<TreeOption[]>(() =>
  PERMISSION_GROUPS.map((g) => ({
    key: g.key,
    label: g.label,
    children: g.permissions.map((p) => ({
      key: p.key,
      label: p.description ? `${p.label}（${p.description}）` : p.label,
      isLeaf: true,
    })),
  })),
);

const totalPermissionCount = computed(() =>
  PERMISSION_GROUPS.reduce((s, g) => s + g.permissions.length, 0),
);

const checkedKeys = ref<string[]>([]);

function onCheckedKeys(keys: Array<string | number>) {
  checkedKeys.value = keys.filter((k) => typeof k === "string") as string[];
}

const pickedPermissionCount = computed(() => {
  const set = new Set(checkedKeys.value);
  let n = 0;
  for (const g of PERMISSION_GROUPS) {
    for (const p of g.permissions) {
      if (set.has(p.key)) n++;
    }
  }
  return n;
});

function handleSelectAll() {
  const keys: string[] = [];
  for (const g of PERMISSION_GROUPS) {
    keys.push(g.key);
    for (const p of g.permissions) keys.push(p.key);
  }
  checkedKeys.value = keys;
}

function handleClear() {
  checkedKeys.value = [];
}

watch(
  () => [props.show, props.role],
  ([show]) => {
    if (!show) return;
    if (props.role) {
      form.name = props.role.name;
      form.display_name = props.role.display_name;
      form.description = props.role.description || "";
      const granted = new Set(props.role.permissions);
      const keys: string[] = [];
      for (const g of PERMISSION_GROUPS) {
        const childKeys = g.permissions.map((p) => p.key);
        const groupHasAny = childKeys.some((k) => granted.has(k));
        const groupAll = childKeys.every((k) => granted.has(k));
        if (groupAll && childKeys.length > 0) keys.push(g.key);
        for (const k of childKeys) if (granted.has(k)) keys.push(k);
        // 若该菜单只有 1 条子权限，组键也应勾上保持视觉一致
        if (groupHasAny && childKeys.length === 1) keys.push(g.key);
      }
      checkedKeys.value = Array.from(new Set(keys));
    } else {
      form.name = "";
      form.display_name = "";
      form.description = "";
      checkedKeys.value = [];
    }
  },
);

function pickPermissionKeys(): string[] {
  const set = new Set(checkedKeys.value);
  const out: string[] = [];
  for (const g of PERMISSION_GROUPS) {
    for (const p of g.permissions) {
      if (set.has(p.key)) out.push(p.key);
    }
  }
  return out;
}

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  submitting.value = true;
  try {
    const payload = {
      display_name: form.display_name,
      description: form.description || undefined,
      permissions: pickPermissionKeys(),
    };
    if (isEdit.value && props.role) {
      const res = await updateRoleApi(props.role.id, payload);
      if (res.success) {
        message.success("角色已更新");
        emit("update:show", false);
        emit("saved");
      }
    } else {
      const res = await createRoleApi({ ...payload, name: form.name });
      if (res.success) {
        message.success("角色已创建");
        emit("update:show", false);
        emit("saved");
      }
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "";
    message.error(msg || (isEdit.value ? "保存失败" : "创建失败"));
  } finally {
    submitting.value = false;
  }
}
</script>

<style scoped>
.role-perm {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.role-perm__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  line-height: 1.6;
}

.role-perm__tree {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 8px 4px;
  max-height: 360px;
  overflow: auto;
  background: var(--bg-card);
}

.role-perm__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.role-perm__count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
