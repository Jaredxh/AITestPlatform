<template>
  <n-drawer v-model:show="visible" :width="620" placement="right">
    <n-drawer-content :title="drawerTitle" closable>
      <n-spin :show="loadingDetail">
        <n-form ref="formRef" :model="form" :rules="rules" label-placement="top">
          <n-form-item label="用例标题" path="title">
            <n-input
              v-model:value="form.title"
              placeholder="请输入用例标题"
              :maxlength="500"
            />
          </n-form-item>

          <div class="flex gap-4">
            <n-form-item label="优先级" path="priority" class="flex-1">
              <n-select
                v-model:value="form.priority"
                :options="priorityOptions"
              />
            </n-form-item>
            <n-form-item v-if="!isNew" label="状态" path="status" class="flex-1">
              <n-select
                v-model:value="form.status"
                :options="statusOptions"
              />
            </n-form-item>
            <n-form-item label="所属模块" class="flex-1">
              <n-tree-select
                v-model:value="form.module_id"
                :options="moduleTreeOptions"
                placeholder="选择模块（可选）"
                clearable
                default-expand-all
              />
            </n-form-item>
          </div>

          <n-form-item label="前置条件">
            <n-input
              v-model:value="form.precondition"
              type="textarea"
              placeholder="用例执行前需要满足的条件"
              :rows="2"
            />
          </n-form-item>

          <n-form-item v-if="hasTestDataPerm">
            <template #label>
              <span>
                默认物料集
                <n-tooltip>
                  <template #trigger>
                    <span
                      class="i-carbon-information-square ml-1 inline-block align-middle"
                    />
                  </template>
                  执行该用例时自动加载的物料集。与环境级 / 项目级 / 个人级 / 执行级按顺序合并；后面的覆盖前面的。Task 9.1 消费。
                </n-tooltip>
              </span>
            </template>
            <set-selector
              v-if="projectStore.currentProjectId"
              v-model="form.default_data_set_ids"
              :project-id="projectStore.currentProjectId"
              :testcase-ids="props.testcaseId ? [props.testcaseId] : undefined"
            />
            <n-alert v-else type="warning" :bordered="false" size="small">
              请先在顶栏选择项目
            </n-alert>
          </n-form-item>

          <!-- Steps -->
          <div class="mb-4">
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm font-medium">测试步骤</span>
              <n-button size="tiny" @click="addStep">
                <template #icon><span class="i-carbon-add" /></template>
                添加步骤
              </n-button>
            </div>

            <div v-if="form.steps.length === 0" class="text-center py-4 text-gray-400 text-sm">
              暂无步骤，点击"添加步骤"开始编写
            </div>

            <div v-for="(step, idx) in form.steps" :key="idx" class="mb-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
              <div class="flex items-center justify-between mb-2">
                <n-tag size="small" :bordered="false">步骤 {{ idx + 1 }}</n-tag>
                <n-button size="tiny" type="error" quaternary @click="removeStep(idx)">
                  <template #icon><span class="i-carbon-close" /></template>
                </n-button>
              </div>
              <n-input
                v-model:value="step.action"
                type="textarea"
                placeholder="操作步骤描述"
                :rows="2"
                class="mb-2"
              />
              <n-input
                v-model:value="step.expected_result"
                type="textarea"
                placeholder="预期结果（可选）"
                :rows="2"
              />
            </div>
          </div>
        </n-form>
      </n-spin>

      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="visible = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="handleSave">
            {{ isNew ? "创建" : "保存" }}
          </n-button>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from "vue";
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSpin,
  NTag,
  NTooltip,
  NTreeSelect,
  useMessage,
} from "naive-ui";
import type { FormRules, TreeSelectOption } from "naive-ui";
import {
  getTestcaseApi,
  createTestcaseApi,
  updateTestcaseApi,
  getModuleTreeApi,
} from "@/services/testcases";
import type { ModuleTreeNode } from "@/services/testcases";
import { useProjectStore } from "@/stores/project";
import { usePermission } from "@/composables/usePermission";
import SetSelector from "@/components/test-data/SetSelector.vue";

const props = defineProps<{
  testcaseId: string | null;
}>();

const emit = defineEmits<{
  (e: "saved"): void;
}>();

const visible = defineModel<boolean>("show", { default: false });
const message = useMessage();
const projectStore = useProjectStore();
const { has } = usePermission();

const hasTestDataPerm = computed(() => has("test_data:view"));

const isNew = computed(() => !props.testcaseId);
const loadingDetail = ref(false);
const saving = ref(false);
const formRef = ref();
const currentDisplayId = ref<string>("");

const drawerTitle = computed(() => {
  if (isNew.value) return "新建测试用例";
  return currentDisplayId.value
    ? `编辑测试用例 · ${currentDisplayId.value}`
    : "编辑测试用例";
});

const form = reactive({
  title: "",
  priority: "medium",
  status: "active",
  module_id: null as string | null,
  precondition: "",
  steps: [] as Array<{ action: string; expected_result: string }>,
  default_data_set_ids: [] as string[],
});

const rules: FormRules = {
  title: { required: true, message: "请输入用例标题", trigger: "blur" },
};

const priorityOptions = [
  { label: "高", value: "high" },
  { label: "中", value: "medium" },
  { label: "低", value: "low" },
];

const statusOptions = [
  { label: "有效", value: "active" },
  { label: "草稿", value: "draft" },
  { label: "废弃", value: "deprecated" },
];

const moduleTree = ref<ModuleTreeNode[]>([]);

function buildTreeSelectOptions(nodes: ModuleTreeNode[]): TreeSelectOption[] {
  return nodes.map((n) => ({
    key: n.id,
    label: n.name,
    children: n.children.length > 0 ? buildTreeSelectOptions(n.children) : undefined,
  }));
}

const moduleTreeOptions = computed(() => buildTreeSelectOptions(moduleTree.value));

async function fetchModuleTree() {
  const projectId = projectStore.currentProjectId;
  if (!projectId) return;
  try {
    const res = await getModuleTreeApi(projectId);
    if (res.success) {
      moduleTree.value = res.data;
    }
  } catch {
    /* ignore */
  }
}

function resetForm() {
  form.title = "";
  form.priority = "medium";
  form.status = "active";
  form.module_id = null;
  form.precondition = "";
  form.steps = [];
  form.default_data_set_ids = [];
  currentDisplayId.value = "";
}

async function loadDetail(id: string) {
  loadingDetail.value = true;
  try {
    const res = await getTestcaseApi(id);
    if (res.success) {
      const tc = res.data;
      form.title = tc.title;
      form.priority = tc.priority;
      form.status = tc.status;
      form.module_id = tc.module_id;
      form.precondition = tc.precondition || "";
      form.steps = tc.steps.map((s) => ({
        action: s.action,
        expected_result: s.expected_result || "",
      }));
      form.default_data_set_ids = [...(tc.default_data_set_ids ?? [])];
      currentDisplayId.value =
        tc.display_id ||
        (tc.case_no ? `TC-${String(tc.case_no).padStart(4, "0")}` : "");
    }
  } catch {
    message.error("获取用例详情失败");
  } finally {
    loadingDetail.value = false;
  }
}

function addStep() {
  form.steps.push({ action: "", expected_result: "" });
}

function removeStep(idx: number) {
  form.steps.splice(idx, 1);
}

async function handleSave() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  const projectId = projectStore.currentProjectId;
  if (!projectId) {
    message.warning("请先选择项目");
    return;
  }

  const stepsPayload = form.steps
    .filter((s) => s.action.trim())
    .map((s, i) => ({
      step_number: i + 1,
      action: s.action,
      expected_result: s.expected_result || null,
    }));

  saving.value = true;
  try {
    if (isNew.value) {
      const res = await createTestcaseApi(projectId, {
        title: form.title,
        priority: form.priority,
        module_id: form.module_id,
        precondition: form.precondition || null,
        steps: stepsPayload,
        default_data_set_ids: form.default_data_set_ids,
      });
      if (res.success) {
        message.success("用例创建成功");
        visible.value = false;
        emit("saved");
      }
    } else {
      const res = await updateTestcaseApi(props.testcaseId!, {
        title: form.title,
        priority: form.priority,
        status: form.status,
        module_id: form.module_id,
        precondition: form.precondition || null,
        steps: stepsPayload,
        default_data_set_ids: form.default_data_set_ids,
      });
      if (res.success) {
        message.success("用例更新成功");
        visible.value = false;
        emit("saved");
      }
    }
  } catch {
    message.error(isNew.value ? "创建失败" : "保存失败");
  } finally {
    saving.value = false;
  }
}

watch(visible, (val) => {
  if (val) {
    fetchModuleTree();
    if (props.testcaseId) {
      loadDetail(props.testcaseId);
    } else {
      resetForm();
    }
  }
});
</script>
