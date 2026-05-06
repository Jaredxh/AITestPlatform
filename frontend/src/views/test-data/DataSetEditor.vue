<template>
  <div>
    <page-header
      :title="detail?.name || '物料集'"
      :subtitle="subtitle"
      icon="i-carbon-data-categorical"
      back
      @back="goBack"
    >
      <template #extra>
        <n-button
          :disabled="!canImport || !detail"
          @click="importVisible = true"
        >
          <template #icon><span class="i-carbon-document-import" /></template>
          批量导入
        </n-button>
        <n-button
          :disabled="!canEdit || !detail"
          @click="openCloneDialog"
        >
          <template #icon><span class="i-carbon-copy" /></template>
          克隆
        </n-button>
        <n-button
          :disabled="!canEdit"
          :loading="savingMeta"
          type="primary"
          secondary
          @click="openMetaEditor"
        >
          <template #icon><span class="i-carbon-edit" /></template>
          编辑元数据
        </n-button>
        <n-button
          :disabled="!canEdit || !detail"
          type="primary"
          @click="openItemCreate"
        >
          <template #icon><span class="i-carbon-add" /></template>
          新增物料
        </n-button>
      </template>
    </page-header>

    <n-spin :show="loading">
      <!-- 元数据摘要卡 -->
      <div v-if="detail" class="dse-meta">
        <div class="dse-meta__row">
          <n-tag :bordered="false" :type="scopeTag.color as 'default' | 'info' | 'success' | 'warning'">
            <template #icon><span :class="scopeTag.icon" /></template>
            {{ scopeTag.label }}
          </n-tag>
          <n-tag v-if="detail.is_default" size="small" :bordered="false" type="success">
            <template #icon><span class="i-carbon-star-filled" /></template>
            项目默认
          </n-tag>
          <n-tag v-if="detail.category" size="small" :bordered="false">
            <template #icon><span class="i-carbon-tag" /></template>
            {{ detail.category }}
          </n-tag>
        </div>
        <p v-if="detail.description" class="dse-meta__desc">
          {{ detail.description }}
        </p>
        <p v-else class="dse-meta__desc dse-meta__desc--placeholder">
          暂无描述。可点击「编辑元数据」补充用例说明、期望数据等上下文。
        </p>
      </div>

      <!-- 物料表格 -->
      <n-card class="dse-items-card" :bordered="false">
        <template #header>
          <div class="dse-items-header">
            <span class="i-carbon-data-table" />
            物料条目
            <span class="dse-items-count">{{ items.length }} 项</span>
          </div>
        </template>

        <app-empty
          v-if="!loading && items.length === 0"
          icon="i-carbon-data-blob"
          title="还没有物料条目"
          description="新增物料：6 种类型（string / multiline / secret / file / random / dataset），对应用例里 {{key}} 的模板变量"
        >
          <template #actions>
            <n-button
              type="primary"
              :disabled="!canEdit"
              @click="openItemCreate"
            >
              <template #icon><span class="i-carbon-add" /></template>
              新增第一个物料
            </n-button>
          </template>
        </app-empty>

        <n-data-table
          v-else
          :columns="columns"
          :data="items"
          :row-key="(row: TestDataItem) => row.id"
          size="small"
          :bordered="false"
          striped
        />
      </n-card>
    </n-spin>

    <!-- 元数据编辑弹窗 -->
    <n-modal
      v-model:show="metaVisible"
      preset="card"
      :style="{ width: '540px' }"
      title="编辑物料集"
      :mask-closable="false"
    >
      <n-form ref="metaFormRef" :model="metaForm" :rules="metaRules" label-placement="left" label-width="80">
        <n-form-item label="名称" path="name">
          <n-input v-model:value="metaForm.name" placeholder="如：登录账号池 / 订单下单测试数据" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input
            v-model:value="metaForm.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="补充用途、负责人、场景等"
          />
        </n-form-item>
        <n-form-item label="分类">
          <n-input v-model:value="metaForm.category" placeholder="可选，用于在主页分组展示" />
        </n-form-item>
        <n-form-item v-if="detail && detail.scope === 'project'" label="项目默认">
          <n-switch v-model:value="metaForm.is_default" />
          <span class="dse-switch-hint">开启后，该项目下执行用例默认合并本物料集</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="dse-modal-footer">
          <n-button @click="metaVisible = false">取消</n-button>
          <n-button type="primary" :loading="savingMeta" @click="handleMetaSave">
            保存
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 新增 / 编辑 物料弹窗 -->
    <n-modal
      v-model:show="itemVisible"
      preset="card"
      :style="{ width: '620px' }"
      :title="editingItem ? '编辑物料' : '新增物料'"
      :mask-closable="false"
    >
      <n-form ref="itemFormRef" :model="itemForm" :rules="itemRules" label-placement="top">
        <n-grid :cols="2" :x-gap="12">
          <n-form-item-gi label="键（key）" path="key" :span="1">
            <n-input
              v-model:value="itemForm.key"
              placeholder="以字母开头的变量名，如 login_username"
              :disabled="!!editingItem"
            />
          </n-form-item-gi>
          <n-form-item-gi label="类型" path="value_type" :span="1">
            <n-select
              v-model:value="itemForm.value_type"
              :options="valueTypeOptions"
              :disabled="!!editingItem"
              :render-label="renderTypeLabel"
            />
          </n-form-item-gi>
        </n-grid>

        <n-form-item label="说明">
          <n-input
            v-model:value="itemForm.description"
            placeholder="告诉 AI 这个 key 代表什么、什么场景要用"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 3 }"
          />
        </n-form-item>

        <n-form-item label="值" :required="true">
          <div class="dse-item-value-slot">
            <n-input
              v-if="itemForm.value_type === 'string'"
              v-model:value="itemForm.value_text"
              placeholder="单行文本"
            />
            <n-input
              v-else-if="itemForm.value_type === 'multiline'"
              v-model:value="itemForm.value_text"
              type="textarea"
              placeholder="多行文本（长 JSON 请用 dataset 类型）"
              :autosize="{ minRows: 3, maxRows: 10 }"
            />
            <secret-field
              v-else-if="itemForm.value_type === 'secret'"
              ref="secretFieldRef"
              :item-id="editingItem?.id"
              :has-secret="
                editingItem?.has_secret_value === true || secretTouched
              "
              :pending-value="itemForm.value_secret"
              @save="handleSecretSave"
              @cancel="handleSecretCancel"
            />
            <file-field
              v-else-if="itemForm.value_type === 'file'"
              :item-id="editingItem?.id"
              :file-path="editingItem?.file_path ?? null"
              :file-size="editingItem?.file_size ?? null"
              :file-mime="editingItem?.file_mime ?? null"
              :disabled="!!editingItem"
              :accept="'*/*'"
              @pick="handleFilePick"
            />
            <random-field
              v-else-if="itemForm.value_type === 'random'"
              v-model="itemForm.value_text as string"
            />
            <dataset-field
              v-else-if="itemForm.value_type === 'dataset'"
              v-model="itemForm.value_json"
            />
          </div>
        </n-form-item>
      </n-form>

      <template #footer>
        <div class="dse-modal-footer">
          <n-button @click="itemVisible = false">取消</n-button>
          <n-button
            type="primary"
            :loading="savingItem"
            :disabled="!canSubmitItem"
            @click="handleItemSave"
          >
            {{ editingItem ? "保存" : "创建" }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <!-- 批量导入 -->
    <import-dialog
      v-if="detail"
      v-model:show="importVisible"
      :set-id="detail.id"
      :set-name="detail.name"
      @imported="handleImported"
    />

    <!-- 克隆 -->
    <n-modal
      v-model:show="cloneVisible"
      preset="card"
      :style="{ width: '520px' }"
      title="克隆物料集"
      :mask-closable="!cloning"
    >
      <n-form
        ref="cloneFormRef"
        :model="cloneForm"
        :rules="cloneRules"
        label-placement="left"
        label-width="90"
      >
        <n-form-item label="新名称" path="new_name">
          <n-input
            v-model:value="cloneForm.new_name"
            placeholder="如：登录账号池（副本）"
          />
        </n-form-item>
        <n-form-item label="可见范围">
          <n-radio-group v-model:value="cloneForm.scope">
            <n-radio value="inherit">继承原物料集</n-radio>
            <n-radio value="personal">转为个人私有</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item label="分类">
          <n-input v-model:value="cloneForm.category" placeholder="可选" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input
            v-model:value="cloneForm.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </n-form-item>
      </n-form>
      <n-alert type="info" :bordered="false" size="small">
        克隆会复制所有条目（含文件的物理副本）；secret 的密文也随之复制，无需重新录入明文。
      </n-alert>
      <template #footer>
        <div class="dse-modal-footer">
          <n-button :disabled="cloning" @click="cloneVisible = false">取消</n-button>
          <n-button type="primary" :loading="cloning" @click="handleClone">
            克隆
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from "vue";
import { useRouter, useRoute } from "vue-router";
import {
  NButton,
  NCard,
  NDataTable,
  NEllipsis,
  NForm,
  NFormItem,
  NFormItemGi,
  NGrid,
  NInput,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { DataTableColumns, FormInst, FormRules, SelectOption } from "naive-ui";

import {
  NAlert,
  NRadio,
  NRadioGroup,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";
import SecretField from "@/components/test-data/SecretField.vue";
import FileField from "@/components/test-data/FileField.vue";
import RandomField from "@/components/test-data/RandomField.vue";
import DatasetField from "@/components/test-data/DatasetField.vue";
import ImportDialog from "@/components/test-data/ImportDialog.vue";

import {
  getSetApi,
  updateSetApi,
  createItemApi,
  updateItemApi,
  deleteItemApi,
  uploadFileItemApi,
  cloneSetApi,
  VALUE_TYPE_META,
  VALUE_TYPES,
  SCOPE_META,
  formatFileSize,
} from "@/services/testData";
import type {
  TestDataSetDetail,
  TestDataItem,
  ValueType,
  ItemCreateParams,
  ItemUpdateParams,
  CloneRequest,
  DataSetScope,
} from "@/services/testData";
import { usePermission } from "@/composables/usePermission";

const router = useRouter();
const route = useRoute();
const message = useMessage();
const { has } = usePermission();

const setId = computed(() => String(route.params.setId || ""));
const projectId = computed(() => String(route.params.projectId || ""));

const loading = ref(false);
const detail = ref<TestDataSetDetail | null>(null);
const items = ref<TestDataItem[]>([]);

const canEdit = computed(() => has("test_data:edit"));
const canImport = computed(() => has("test_data:import"));

const scopeTag = computed(() => {
  const scope = detail.value?.scope ?? "project";
  return SCOPE_META[scope as keyof typeof SCOPE_META];
});

const subtitle = computed(() => {
  if (!detail.value) return "";
  const scope = SCOPE_META[detail.value.scope as keyof typeof SCOPE_META];
  const cnt = items.value.length;
  return `${scope.label} · ${cnt} 项物料`;
});

// ─── 列表加载 ────────────────────────────────────────────────────────

async function fetchDetail() {
  if (!setId.value) return;
  loading.value = true;
  try {
    const res = await getSetApi(setId.value);
    if (res.success) {
      detail.value = res.data;
      items.value = [...res.data.items].sort(
        (a, b) => a.sort_order - b.sort_order || a.key.localeCompare(b.key),
      );
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "加载物料集失败");
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  fetchDetail();
});

function goBack() {
  if (projectId.value) {
    router.push({
      name: "TestDataView",
      params: { projectId: projectId.value },
    });
  } else {
    router.back();
  }
}

// ─── 元数据编辑 ──────────────────────────────────────────────────────

const metaVisible = ref(false);
const metaFormRef = ref<FormInst | null>(null);
const savingMeta = ref(false);
const metaForm = ref({
  name: "",
  description: "",
  category: "",
  is_default: false,
});
const metaRules: FormRules = {
  name: [
    { required: true, message: "请填写名称", trigger: "blur" },
    { min: 1, max: 100, message: "长度 1-100" },
  ],
};

function openMetaEditor() {
  if (!detail.value) return;
  metaForm.value = {
    name: detail.value.name,
    description: detail.value.description ?? "",
    category: detail.value.category ?? "",
    is_default: detail.value.is_default,
  };
  metaVisible.value = true;
}

async function handleMetaSave() {
  await metaFormRef.value?.validate();
  if (!detail.value) return;
  savingMeta.value = true;
  try {
    const res = await updateSetApi(detail.value.id, {
      name: metaForm.value.name,
      description: metaForm.value.description || null,
      category: metaForm.value.category || null,
      is_default:
        detail.value.scope === "project" ? metaForm.value.is_default : undefined,
    });
    if (res.success) {
      message.success("已更新");
      detail.value = res.data;
      metaVisible.value = false;
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "保存失败");
  } finally {
    savingMeta.value = false;
  }
}

// ─── 物料新增 / 编辑 ─────────────────────────────────────────────────

const itemVisible = ref(false);
const itemFormRef = ref<FormInst | null>(null);
const savingItem = ref(false);
const editingItem = ref<TestDataItem | null>(null);
const secretFieldRef = ref<InstanceType<typeof SecretField> | null>(null);
const secretTouched = ref(false);
const pickedFile = ref<File | null>(null);

interface ItemFormShape {
  key: string;
  value_type: ValueType;
  description: string;
  value_text: string;
  value_secret: string;
  value_json: unknown;
}

const itemForm = ref<ItemFormShape>({
  key: "",
  value_type: "string",
  description: "",
  value_text: "",
  value_secret: "",
  value_json: null,
});

const itemRules: FormRules = {
  key: [
    { required: true, message: "请填写 key", trigger: "blur" },
    {
      validator: (_r, v: string) => {
        if (!v) return true;
        if (!/^[A-Za-z][A-Za-z0-9_]{0,99}$/.test(v)) {
          return new Error("key 必须以字母开头，仅含字母、数字、下划线");
        }
        return true;
      },
      trigger: "blur",
    },
  ],
};

const valueTypeOptions: SelectOption[] = VALUE_TYPES.map((t) => ({
  label: VALUE_TYPE_META[t].label,
  value: t,
}));

function renderTypeLabel(opt: SelectOption) {
  const meta = VALUE_TYPE_META[opt.value as ValueType];
  return h(
    "div",
    { style: "display:flex;align-items:center;gap:8px;" },
    [
      h("span", { class: meta.icon, style: "color:var(--brand-primary);" }),
      h("span", {}, meta.label),
      h(
        "span",
        { style: "color:var(--text-tertiary);font-size:12px;margin-left:auto;" },
        meta.description,
      ),
    ],
  );
}

function resetItemForm() {
  itemForm.value = {
    key: "",
    value_type: "string",
    description: "",
    value_text: "",
    value_secret: "",
    value_json: null,
  };
  editingItem.value = null;
  secretTouched.value = false;
  pickedFile.value = null;
}

function openItemCreate() {
  resetItemForm();
  itemVisible.value = true;
}

function openItemEdit(item: TestDataItem) {
  resetItemForm();
  editingItem.value = item;
  itemForm.value = {
    key: item.key,
    value_type: item.value_type,
    description: item.description ?? "",
    value_text: item.value_text ?? "",
    value_secret: "",
    value_json: item.value_json ?? null,
  };
  itemVisible.value = true;
}

function handleSecretSave(plain: string) {
  itemForm.value.value_secret = plain;
  secretTouched.value = true;
  message.success("密值已暂存，点击「保存」后生效");
}

function handleSecretCancel() {
  itemForm.value.value_secret = "";
  secretTouched.value = false;
}

function handleFilePick(file: File) {
  pickedFile.value = file;
  message.success(`已选择文件：${file.name}（${formatFileSize(file.size)}）`);
}

const canSubmitItem = computed(() => {
  if (!itemForm.value.key) return false;
  const vt = itemForm.value.value_type;
  if (editingItem.value) return true;
  // 创建新 item 时对必填值做粗校验
  if (vt === "file") return !!pickedFile.value;
  if (vt === "secret") return !!itemForm.value.value_secret;
  if (vt === "dataset")
    return itemForm.value.value_json !== null && itemForm.value.value_json !== undefined;
  // string / multiline / random：允许空值（比如空字符串占位）
  return true;
});

async function handleItemSave() {
  await itemFormRef.value?.validate();
  if (!detail.value) return;
  savingItem.value = true;
  try {
    if (editingItem.value) {
      await saveExistingItem(editingItem.value);
    } else {
      await createNewItem();
    }
    itemVisible.value = false;
    await fetchDetail();
  } catch (err) {
    message.error(err instanceof Error ? err.message : "保存失败");
  } finally {
    savingItem.value = false;
  }
}

async function createNewItem() {
  if (!detail.value) return;
  const vt = itemForm.value.value_type;

  // file 单独走 multipart
  if (vt === "file") {
    if (!pickedFile.value) throw new Error("请先选择文件");
    const res = await uploadFileItemApi(detail.value.id, {
      key: itemForm.value.key,
      file: pickedFile.value,
      description: itemForm.value.description || undefined,
    });
    if (res.success) message.success("物料已创建");
    return;
  }

  const payload: ItemCreateParams = {
    key: itemForm.value.key,
    value_type: vt,
    description: itemForm.value.description || null,
  };
  if (vt === "string" || vt === "multiline" || vt === "random") {
    payload.value_text = itemForm.value.value_text ?? "";
  } else if (vt === "secret") {
    payload.value_secret = itemForm.value.value_secret;
  } else if (vt === "dataset") {
    payload.value_json = itemForm.value.value_json;
  }
  const res = await createItemApi(detail.value.id, payload);
  if (res.success) message.success("物料已创建");
}

async function saveExistingItem(orig: TestDataItem) {
  const vt = orig.value_type;
  const payload: ItemUpdateParams = {
    key: itemForm.value.key !== orig.key ? itemForm.value.key : undefined,
    description:
      itemForm.value.description !== (orig.description ?? "")
        ? itemForm.value.description || null
        : undefined,
  };

  if (vt === "string" || vt === "multiline" || vt === "random") {
    if (itemForm.value.value_text !== (orig.value_text ?? "")) {
      payload.value_text = itemForm.value.value_text;
    }
  } else if (vt === "secret") {
    if (secretTouched.value && itemForm.value.value_secret) {
      payload.value_secret = itemForm.value.value_secret;
    }
  } else if (vt === "dataset") {
    // JSON 比较：任一方变化都下发
    const before = JSON.stringify(orig.value_json ?? null);
    const after = JSON.stringify(itemForm.value.value_json ?? null);
    if (before !== after) {
      payload.value_json = itemForm.value.value_json;
      if (itemForm.value.value_json === null) payload.clear_value_json = true;
    }
  }

  // 没变化就不请求
  const hasChange =
    payload.key !== undefined ||
    payload.description !== undefined ||
    payload.value_text !== undefined ||
    payload.value_secret !== undefined ||
    payload.value_json !== undefined;
  if (!hasChange) {
    message.info("没有变更");
    return;
  }

  const res = await updateItemApi(orig.id, payload);
  if (res.success) message.success("已更新");
}

// ─── 批量导入 ────────────────────────────────────────────────────────

const importVisible = ref(false);

function handleImported() {
  // 有实际变更 → 拉一次详情刷新表格
  fetchDetail();
}

// ─── 克隆 ────────────────────────────────────────────────────────────

const cloneVisible = ref(false);
const cloning = ref(false);
const cloneFormRef = ref<FormInst | null>(null);
const cloneForm = ref<{
  new_name: string;
  description: string;
  category: string;
  scope: "inherit" | "personal";
}>({
  new_name: "",
  description: "",
  category: "",
  scope: "inherit",
});
const cloneRules: FormRules = {
  new_name: [
    { required: true, message: "请填写新物料集名称", trigger: "blur" },
    { min: 1, max: 200, message: "长度 1-200" },
  ],
};

function openCloneDialog() {
  if (!detail.value) return;
  cloneForm.value = {
    new_name: `${detail.value.name}（副本）`,
    description: detail.value.description ?? "",
    category: detail.value.category ?? "",
    scope: "inherit",
  };
  cloneVisible.value = true;
}

async function handleClone() {
  await cloneFormRef.value?.validate();
  if (!detail.value) return;
  cloning.value = true;
  try {
    const payload: CloneRequest = {
      new_name: cloneForm.value.new_name,
      description: cloneForm.value.description || null,
      category: cloneForm.value.category || null,
      scope:
        cloneForm.value.scope === "personal"
          ? ("personal" as DataSetScope)
          : null,
    };
    const res = await cloneSetApi(detail.value.id, payload);
    if (res.success) {
      message.success("已克隆，正在跳转到新物料集");
      cloneVisible.value = false;
      // 跳到副本编辑页
      router.push({
        name: "TestDataSetEditor",
        params: { projectId: res.data.project_id, setId: res.data.id },
      });
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "克隆失败");
  } finally {
    cloning.value = false;
  }
}

// ─── 行操作（删除）───────────────────────────────────────────────────

async function handleDelete(item: TestDataItem) {
  try {
    const res = await deleteItemApi(item.id);
    if (res.success) {
      message.success("物料已删除");
      await fetchDetail();
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "删除失败");
  }
}

// ─── 表格列 ──────────────────────────────────────────────────────────

const columns = computed<DataTableColumns<TestDataItem>>(() => [
  {
    title: "Key",
    key: "key",
    width: 180,
    render(row) {
      return h("code", { class: "dse-key" }, row.key);
    },
  },
  {
    title: "类型",
    key: "value_type",
    width: 120,
    render(row) {
      const m = VALUE_TYPE_META[row.value_type as ValueType];
      if (!m) return row.value_type;
      return h(NTag, { size: "small", bordered: false }, {
        default: () => m.label,
        icon: () => h("span", { class: m.icon }),
      });
    },
  },
  {
    title: "值预览",
    key: "value",
    render(row) {
      return renderValuePreview(row);
    },
  },
  {
    title: "说明",
    key: "description",
    width: 220,
    render(row) {
      if (!row.description) {
        return h("span", { style: "color:var(--text-tertiary);" }, "—");
      }
      return h(NEllipsis, { tooltip: true, style: "max-width:200px;" }, {
        default: () => row.description,
      });
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 140,
    render(row) {
      return h("div", { style: "display:flex;gap:4px;" }, [
        h(
          NButton,
          {
            size: "tiny",
            quaternary: true,
            disabled: !canEdit.value,
            onClick: () => openItemEdit(row),
          },
          {
            default: () => "编辑",
            icon: () => h("span", { class: "i-carbon-edit" }),
          },
        ),
        h(
          NPopconfirm,
          {
            onPositiveClick: () => handleDelete(row),
          },
          {
            trigger: () =>
              h(
                NButton,
                {
                  size: "tiny",
                  quaternary: true,
                  type: "error",
                  disabled: !canEdit.value,
                },
                {
                  default: () => "删除",
                  icon: () => h("span", { class: "i-carbon-trash-can" }),
                },
              ),
            default: () => `确认删除物料「${row.key}」？`,
          },
        ),
      ]);
    },
  },
]);

function renderValuePreview(row: TestDataItem) {
  const maxChars = 120;
  switch (row.value_type) {
    case "string":
    case "multiline": {
      const text = row.value_text ?? "";
      if (!text)
        return h("span", { style: "color:var(--text-tertiary);" }, "（空）");
      return h(
        "span",
        { class: "dse-preview-text" },
        text.length > maxChars ? text.slice(0, maxChars) + "…" : text,
      );
    }
    case "random": {
      const tpl = row.value_text ?? "";
      return h("code", { class: "dse-preview-template" }, tpl || "（无模板）");
    }
    case "secret":
      return h(NTag, { size: "small", type: "warning", bordered: false }, {
        default: () => (row.has_secret_value ? "●●●●●● 已加密" : "未设置"),
        icon: () => h("span", { class: "i-carbon-password" }),
      });
    case "file": {
      if (!row.file_path) {
        return h(
          "span",
          { style: "color:var(--text-tertiary);" },
          "（尚未上传）",
        );
      }
      const parts = row.file_path.split("/");
      const base = parts[parts.length - 1] ?? row.file_path;
      // 去掉 uuid_ 前缀（32 位 hex）
      const sep = "_";
      const idx = base.indexOf(sep);
      const display =
        idx === 32 && /^[0-9a-f]+$/i.test(base.slice(0, idx))
          ? base.slice(idx + 1)
          : base;
      return h(
        "span",
        { style: "display:flex;gap:6px;align-items:center;" },
        [
          h("span", { class: "i-carbon-document" }),
          h("span", display),
          h(
            "span",
            { style: "color:var(--text-tertiary);font-size:12px;" },
            `（${formatFileSize(row.file_size)}）`,
          ),
        ],
      );
    }
    case "dataset": {
      const val = row.value_json;
      if (val === null || val === undefined) {
        return h("span", { style: "color:var(--text-tertiary);" }, "（空）");
      }
      const text = JSON.stringify(val);
      const len = Array.isArray(val) ? `${val.length} 行` : "object";
      return h(
        NTooltip,
        {},
        {
          trigger: () =>
            h("div", { style: "display:flex;gap:6px;align-items:center;" }, [
              h("span", { class: "i-carbon-data-table" }),
              h("span", len),
              h(
                "code",
                { class: "dse-preview-template" },
                text.length > maxChars ? text.slice(0, maxChars) + "…" : text,
              ),
            ]),
          default: () =>
            h(
              "pre",
              {
                style:
                  "max-width:480px;max-height:240px;overflow:auto;white-space:pre-wrap;margin:0;font-size:12px;",
              },
              JSON.stringify(val, null, 2),
            ),
        },
      );
    }
    default:
      return h("span", {}, "—");
  }
}
</script>

<style scoped>
.dse-meta {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
  margin-bottom: 16px;
}

.dse-meta__row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.dse-meta__desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.dse-meta__desc--placeholder {
  color: var(--text-tertiary);
  font-style: italic;
}

.dse-items-card :deep(.n-card__content) {
  padding: 0 16px 16px;
}

.dse-items-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-primary);
}

.dse-items-count {
  color: var(--text-tertiary);
  font-size: 13px;
  font-weight: 400;
  margin-left: auto;
}

.dse-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.dse-switch-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.dse-item-value-slot {
  width: 100%;
}

:deep(.dse-key) {
  font-family: var(--font-mono, monospace);
  background: var(--bg-active);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12.5px;
  color: var(--brand-primary);
}

:deep(.dse-preview-text) {
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.5;
  word-break: break-all;
}

:deep(.dse-preview-template) {
  font-family: var(--font-mono, monospace);
  background: var(--bg-active);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-all;
}
</style>
