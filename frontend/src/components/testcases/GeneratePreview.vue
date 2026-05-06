<template>
  <div>
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-2">
        <n-text class="font-medium">生成结果</n-text>
        <n-tag size="small" :bordered="false">{{ cases.length }} 条用例</n-tag>
      </div>
      <div class="flex gap-2">
        <n-button size="small" @click="$emit('close')">关闭</n-button>
        <n-button
          type="primary"
          size="small"
          :loading="accepting"
          :disabled="cases.length === 0"
          @click="$emit('accept-all')"
        >
          <template #icon><span class="i-carbon-checkmark-filled" /></template>
          全部接受 ({{ cases.length }})
        </n-button>
      </div>
    </div>

    <div class="space-y-3 max-h-[60vh] overflow-auto pr-1">
      <n-card
        v-for="(tc, idx) in cases"
        :key="idx"
        size="small"
        :bordered="true"
        class="generated-case-card"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="flex-1 min-w-0">
            <!-- Title & priority -->
            <div class="flex items-center gap-2 mb-2">
              <n-text class="font-medium text-sm">{{ tc.title }}</n-text>
              <n-tag
                size="tiny"
                :type="priorityType(tc.priority)"
                :bordered="false"
              >
                {{ priorityLabel(tc.priority) }}
              </n-tag>
            </div>

            <!-- Precondition -->
            <div v-if="tc.precondition" class="mb-2">
              <n-text depth="3" class="text-xs">前置条件：</n-text>
              <n-text class="text-xs">{{ tc.precondition }}</n-text>
            </div>

            <!-- Steps -->
            <div v-if="tc.steps && tc.steps.length > 0">
              <n-text depth="3" class="text-xs block mb-1">
                步骤 ({{ tc.steps.length }})
              </n-text>
              <div
                v-for="(step, si) in tc.steps"
                :key="si"
                class="text-xs mb-1 pl-2 border-l-2 border-gray-200 dark:border-gray-600"
              >
                <div>
                  <span class="text-gray-400 mr-1">{{ step.step_number }}.</span>
                  {{ step.action }}
                </div>
                <div v-if="step.expected_result" class="text-green-600 dark:text-green-400 mt-0.5">
                  → {{ step.expected_result }}
                </div>
              </div>
            </div>
          </div>

          <!-- Actions -->
          <div class="flex flex-col gap-1 shrink-0">
            <n-button
              size="tiny"
              type="primary"
              :loading="accepting"
              @click="$emit('accept', [idx])"
            >
              接受
            </n-button>
            <n-button size="tiny" @click="openEdit(idx)">
              编辑
            </n-button>
            <n-button size="tiny" type="error" ghost @click="$emit('remove', idx)">
              移除
            </n-button>
          </div>
        </div>
      </n-card>
    </div>

    <!-- Inline edit modal -->
    <n-modal v-model:show="showEditModal" preset="card" title="编辑用例" style="width: 560px;">
      <n-form v-if="editForm" label-placement="top">
        <n-form-item label="标题">
          <n-input v-model:value="editForm.title" />
        </n-form-item>
        <div class="flex gap-4">
          <n-form-item label="优先级" class="w-32">
            <n-select v-model:value="editForm.priority" :options="priorityOptions" />
          </n-form-item>
          <n-form-item label="前置条件" class="flex-1">
            <n-input v-model:value="editForm.precondition" />
          </n-form-item>
        </div>
        <n-form-item label="步骤">
          <div class="w-full space-y-2">
            <div
              v-for="(step, si) in editForm.steps"
              :key="si"
              class="flex gap-2 items-start"
            >
              <n-input-number
                v-model:value="step.step_number"
                :min="1"
                size="small"
                class="w-16"
              />
              <n-input
                v-model:value="step.action"
                placeholder="操作"
                size="small"
                class="flex-1"
              />
              <n-input
                v-model:value="step.expected_result"
                placeholder="预期结果"
                size="small"
                class="flex-1"
              />
              <n-button size="small" quaternary type="error" @click="editForm.steps.splice(si, 1)">
                <template #icon><span class="i-carbon-close" /></template>
              </n-button>
            </div>
            <n-button size="tiny" dashed @click="addEditStep">+ 添加步骤</n-button>
          </div>
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="showEditModal = false">取消</n-button>
          <n-button type="primary" @click="confirmEdit">保存</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from "vue";
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NTag,
  NText,
} from "naive-ui";
import type { GeneratedTestcase } from "@/services/testcases";

const props = defineProps<{
  cases: (GeneratedTestcase & { _selected?: boolean })[];
  batchId: string;
  moduleId: string | null;
  accepting: boolean;
}>();

const emit = defineEmits<{
  (e: "accept", indices: number[]): void;
  (e: "accept-all"): void;
  (e: "remove", idx: number): void;
  (e: "edit", idx: number, updated: GeneratedTestcase): void;
  (e: "close"): void;
}>();

const priorityOptions = [
  { label: "高", value: "high" },
  { label: "中", value: "medium" },
  { label: "低", value: "low" },
];

function priorityLabel(p?: string) {
  const map: Record<string, string> = { high: "高", medium: "中", low: "低" };
  return map[p || "medium"] || p;
}

function priorityType(p?: string): "error" | "warning" | "info" {
  const map: Record<string, "error" | "warning" | "info"> = {
    high: "error",
    medium: "warning",
    low: "info",
  };
  return map[p || "medium"] || "info";
}

// ── Edit modal ──

const showEditModal = ref(false);
const editingIndex = ref(-1);
const editForm = reactive({
  title: "",
  precondition: "" as string | null,
  priority: "medium",
  steps: [] as Array<{ step_number: number; action: string; expected_result: string | null }>,
});

function openEdit(idx: number) {
  editingIndex.value = idx;
  const tc = props.cases[idx];
  editForm.title = tc.title;
  editForm.precondition = tc.precondition || "";
  editForm.priority = tc.priority || "medium";
  editForm.steps = (tc.steps || []).map((s) => ({
    step_number: s.step_number,
    action: s.action,
    expected_result: s.expected_result ?? null,
  }));
  showEditModal.value = true;
}

function addEditStep() {
  editForm.steps.push({
    step_number: editForm.steps.length + 1,
    action: "",
    expected_result: null,
  });
}

function confirmEdit() {
  const updated: GeneratedTestcase = {
    title: editForm.title,
    precondition: editForm.precondition || null,
    priority: editForm.priority,
    steps: editForm.steps
      .filter((s) => s.action.trim())
      .map((s) => ({ ...s, expected_result: s.expected_result ?? null })),
  };
  emit("edit", editingIndex.value, updated);
  showEditModal.value = false;
}
</script>

<style scoped>
.generated-case-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
</style>
