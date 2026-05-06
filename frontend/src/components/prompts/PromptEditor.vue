<template>
  <n-modal v-model:show="visible" preset="card" :title="title" style="width: 820px" :segmented="{ content: true }">
    <n-form ref="formRef" :model="form" :rules="formRules" label-placement="left" label-width="80">
      <n-form-item label="名称" path="name">
        <n-input v-model:value="form.name" placeholder="提示词名称" maxlength="200" />
      </n-form-item>
      <n-form-item label="描述" path="description">
        <n-input v-model:value="form.description" type="textarea" placeholder="简要描述用途（可选）" :rows="2" />
      </n-form-item>
      <n-grid :cols="2" :x-gap="16">
        <n-gi>
          <n-form-item label="分类" path="category">
            <n-select
              v-model:value="form.category"
              :options="categoryOptions"
              :disabled="isSystem"
              placeholder="选择分类"
              @update:value="onCategoryChange"
            />
          </n-form-item>
        </n-gi>
        <n-gi>
          <n-form-item label="子分类" path="sub_category">
            <n-select
              v-if="subCategoryOptions.length > 0"
              v-model:value="form.sub_category"
              :options="subCategoryOptions"
              placeholder="选择子分类（可选）"
              :disabled="isSystem"
              clearable
              filterable
              tag
            />
            <n-input
              v-else
              v-model:value="form.sub_category"
              placeholder="自定义子分类（选填）"
              :disabled="isSystem"
            />
          </n-form-item>
        </n-gi>
      </n-grid>
      <n-form-item label="选项">
        <n-space>
          <n-checkbox v-model:checked="form.auto_apply">自动调用</n-checkbox>
          <n-checkbox v-model:checked="form.is_default">设为默认</n-checkbox>
        </n-space>
      </n-form-item>

      <n-form-item label="可用变量">
        <div class="prompt-vars">
          <div class="prompt-vars__hint">
            点击下方变量将其插入到内容中，AI 调用时会自动用上下文替换。
          </div>
          <div class="prompt-vars__list">
            <n-popover
              v-for="meta in availableVariables"
              :key="meta.name"
              trigger="hover"
              placement="top"
              :delay="200"
            >
              <template #trigger>
                <button
                  type="button"
                  class="prompt-var-chip"
                  :class="`prompt-var-chip--${meta.source}`"
                  @click="insertVariable(meta.name)"
                >
                  <span class="prompt-var-chip__name" v-text="varToken(meta.name)" />
                  <span class="prompt-var-chip__label">{{ meta.label }}</span>
                </button>
              </template>
              <div class="prompt-var-tip">
                <div class="prompt-var-tip__title">
                  <span class="font-mono" v-text="varToken(meta.name)" />
                  <n-tag size="tiny" :bordered="false" :type="sourceTagType(meta.source)">
                    {{ sourceLabel(meta.source) }}
                  </n-tag>
                </div>
                <div class="prompt-var-tip__desc">{{ meta.description }}</div>
              </div>
            </n-popover>
          </div>
        </div>
      </n-form-item>

      <n-form-item label="内容" path="content">
        <n-input
          ref="contentInputRef"
          v-model:value="form.content"
          type="textarea"
          placeholder="提示词内容，支持 {{变量名}} 占位符"
          :rows="14"
          class="font-mono text-sm"
        />
      </n-form-item>
      <n-form-item v-if="isEditing" label="变更说明">
        <n-input v-model:value="form.change_note" placeholder="描述本次修改（可选）" />
      </n-form-item>
    </n-form>
    <template #action>
      <div class="flex justify-end gap-2">
        <n-button @click="visible = false">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="handleSubmit">
          {{ isEditing ? "保存" : "创建" }}
        </n-button>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, reactive, watch, computed, nextTick } from "vue";
import {
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NGrid,
  NGi,
  NCheckbox,
  NSpace,
  NPopover,
  NTag,
  NButton,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";
import { createPromptApi, updatePromptApi } from "@/services/prompts";
import type { PromptInfo } from "@/services/prompts";
import {
  PROMPT_CATEGORY_OPTIONS,
  PROMPT_SUB_CATEGORY_OPTIONS,
  BUILT_IN_PROMPT_VARIABLES,
  VARIABLE_SOURCE_LABEL,
  type PromptVariableMeta,
} from "@/constants/prompts";

const props = defineProps<{
  projectId: string;
  prompt: PromptInfo | null;
}>();

const emit = defineEmits<{
  saved: [];
}>();

const visible = defineModel<boolean>("show", { default: false });
const message = useMessage();
const formRef = ref<FormInst | null>(null);
const contentInputRef = ref<InstanceType<typeof NInput> | null>(null);
const submitting = ref(false);

const isEditing = computed(() => !!props.prompt);
const isSystem = computed(() => props.prompt?.is_system ?? false);
const title = computed(() => (isEditing.value ? `编辑提示词：${props.prompt?.name}` : "新建提示词"));

const form = reactive({
  name: "",
  description: "",
  content: "",
  category: "custom",
  sub_category: "" as string | null,
  auto_apply: false,
  is_default: false,
  change_note: "",
});

const categoryOptions = PROMPT_CATEGORY_OPTIONS;

const subCategoryOptions = computed(() => PROMPT_SUB_CATEGORY_OPTIONS[form.category] || []);

function onCategoryChange() {
  // 切换分类时清空子分类，避免遗留值
  form.sub_category = "";
}

const availableVariables = computed<PromptVariableMeta[]>(() => {
  // 按当前 category 过滤；同时把 prompt 上声明的 extra 变量也展示
  const scope: PromptVariableMeta["scope"][] = ["common"];
  if (form.category === "review") scope.push("review");
  if (form.category === "generation" || form.category === "ui_test") scope.push("generation");
  if (form.category === "chat") scope.push("chat");

  const builtIn = BUILT_IN_PROMPT_VARIABLES.filter((v) => scope.includes(v.scope));
  const extras: PromptVariableMeta[] = (props.prompt?.variables || [])
    .filter((v) => !builtIn.some((b) => b.name === v.name))
    .map((v) => ({
      name: v.name,
      label: v.label || v.name,
      description: "由当前提示词自定义的变量。",
      source: (v.source as PromptVariableMeta["source"]) || "manual",
      scope: "common",
    }));
  return [...builtIn, ...extras];
});

const formRules: FormRules = {
  name: [{ required: true, message: "请输入名称", trigger: "blur" }],
  content: [{ required: true, message: "请输入内容", trigger: "blur" }],
  category: [{ required: true, message: "请选择分类", trigger: "change" }],
};

function sourceLabel(s: PromptVariableMeta["source"]) {
  return VARIABLE_SOURCE_LABEL[s];
}

function varToken(name: string): string {
  return `\u007B\u007B${name}\u007D\u007D`;
}

function sourceTagType(
  s: PromptVariableMeta["source"],
): "info" | "success" | "warning" | "default" {
  return s === "context" ? "info" : s === "auto" ? "success" : "warning";
}

watch(
  () => [visible.value, props.prompt],
  ([show]) => {
    if (show) {
      if (props.prompt) {
        form.name = props.prompt.name;
        form.description = props.prompt.description || "";
        form.content = props.prompt.content;
        form.category = props.prompt.category;
        form.sub_category = props.prompt.sub_category || "";
        form.auto_apply = props.prompt.auto_apply;
        form.is_default = props.prompt.is_default;
        form.change_note = "";
      } else {
        form.name = "";
        form.description = "";
        form.content = "";
        form.category = "custom";
        form.sub_category = "";
        form.auto_apply = false;
        form.is_default = false;
        form.change_note = "";
      }
    }
  },
);

function insertVariable(varName: string) {
  const text = `{{${varName}}}`;
  const el = contentInputRef.value?.$el?.querySelector("textarea") as HTMLTextAreaElement | null;
  if (el) {
    const start = el.selectionStart;
    const end = el.selectionEnd;
    form.content = form.content.slice(0, start) + text + form.content.slice(end);
    nextTick(() => {
      el.focus();
      el.setSelectionRange(start + text.length, start + text.length);
    });
  } else {
    form.content += text;
  }
}

async function handleSubmit() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }

  submitting.value = true;
  try {
    if (isEditing.value && props.prompt) {
      const res = await updatePromptApi(props.prompt.id, {
        name: form.name,
        description: form.description || undefined,
        content: form.content,
        category: form.category,
        sub_category: form.sub_category || undefined,
        auto_apply: form.auto_apply,
        is_default: form.is_default,
        change_note: form.change_note || undefined,
      });
      if (res.success) {
        message.success("提示词更新成功");
        visible.value = false;
        emit("saved");
      }
    } else {
      const res = await createPromptApi(props.projectId, {
        name: form.name,
        description: form.description || undefined,
        content: form.content,
        category: form.category,
        sub_category: form.sub_category || undefined,
        auto_apply: form.auto_apply,
        is_default: form.is_default,
      });
      if (res.success) {
        message.success("提示词创建成功");
        visible.value = false;
        emit("saved");
      }
    }
  } catch {
    message.error(isEditing.value ? "更新失败" : "创建失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<style scoped>
.prompt-vars {
  width: 100%;
}

.prompt-vars__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.prompt-vars__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.prompt-var-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background: var(--bg-page-soft);
  cursor: pointer;
  font-size: 12px;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    border-color var(--duration-fast) var(--easing-standard);
  font: inherit;
  color: var(--text-primary);
}

.prompt-var-chip:hover {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary-border);
}

.prompt-var-chip__name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--brand-primary);
}

.prompt-var-chip__label {
  color: var(--text-secondary);
}

.prompt-var-chip--auto .prompt-var-chip__name {
  color: var(--color-success);
}

.prompt-var-chip--manual .prompt-var-chip__name {
  color: var(--color-warning);
}

.prompt-var-tip {
  max-width: 320px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.prompt-var-tip__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.prompt-var-tip__desc {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.55;
}
</style>
