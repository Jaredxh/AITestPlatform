<template>
  <n-modal
    :show="visible"
    preset="dialog"
    :title="isEdit ? '编辑 LLM 配置' : '添加 LLM 配置'"
    :show-icon="false"
    style="width: 560px"
    @update:show="$emit('update:visible', $event)"
  >
    <n-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-placement="left"
      label-width="100"
      class="mt-4"
    >
      <n-form-item label="配置名称" path="name">
        <n-input v-model:value="form.name" placeholder="例：DeepSeek 主力" maxlength="100" />
      </n-form-item>

      <n-form-item label="供应商" path="provider">
        <n-select v-model:value="form.provider" :options="providerOptions" @update:value="onProviderChange" />
      </n-form-item>

      <n-form-item label="模型" path="model">
        <n-auto-complete
          v-model:value="form.model"
          :options="modelSuggestions"
          placeholder="例：deepseek-chat"
        />
      </n-form-item>

      <n-form-item label="API Key" path="api_key">
        <n-input
          v-model:value="form.api_key"
          type="password"
          show-password-on="click"
          :placeholder="isEdit && editConfig?.has_api_key ? '已保存，留空表示不修改' : '输入 API Key'"
        />
      </n-form-item>

      <n-form-item label="API 地址" path="base_url">
        <n-input v-model:value="form.base_url" :placeholder="defaultBaseUrl" />
      </n-form-item>

      <n-form-item label="上下文长度" path="context_length_k">
        <n-input-number
          v-model:value="form.context_length_k"
          :min="1"
          :step="1"
          class="flex-1"
          :show-button="true"
          @update:value="onContextLengthChange"
        >
          <template #suffix>K</template>
        </n-input-number>
        <n-tooltip placement="right">
          <template #trigger>
            <span class="i-carbon-information ml-2 text-gray-400 cursor-help" />
          </template>
          模型单次对话允许的最大 token 上限（包括 prompt + 输出）。可按模型实际能力填写，例如 32K / 128K / 200K / 1000K。
        </n-tooltip>
      </n-form-item>

      <n-form-item label="设为默认" path="is_default">
        <n-switch v-model:value="form.is_default" />
        <span class="ml-2 text-xs text-gray-400">默认配置将在新对话中自动使用</span>
      </n-form-item>
    </n-form>

    <template #action>
      <div class="flex justify-between w-full">
        <n-button
          :loading="testing"
          :disabled="!form.provider || !form.model"
          @click="handleTest"
        >
          <template #icon><span class="i-carbon-connection-signal" /></template>
          测试连接
        </n-button>
        <div class="flex gap-2">
          <n-button @click="$emit('update:visible', false)">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="handleSubmit">
            {{ isEdit ? "保存" : "创建" }}
          </n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from "vue";
import {
  NAutoComplete,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSwitch,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { FormInst, FormRules } from "naive-ui";
import {
  createLLMConfigApi,
  updateLLMConfigApi,
  testNewConfigApi,
  testSavedConfigApi,
} from "@/services/llm";
import type { LLMConfigInfo } from "@/services/llm";

const props = defineProps<{
  visible: boolean;
  editConfig: LLMConfigInfo | null;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  saved: [];
}>();

const message = useMessage();
const formRef = ref<FormInst | null>(null);
const submitting = ref(false);
const testing = ref(false);

const isEdit = computed(() => !!props.editConfig);

/**
 * 把 max_tokens 直接当作"上下文 token 上限"暴露给用户，单位 K。
 * 内部下发到后端字段仍是 max_tokens（无需迁移）。temperature 取默认 0.7 不再暴露。
 */
const form = reactive({
  name: "",
  provider: "deepseek",
  model: "",
  api_key: "",
  base_url: "",
  context_length_k: 32,
  is_default: false,
});

const rules: FormRules = {
  name: [{ required: true, message: "请输入配置名称", trigger: "blur" }],
  provider: [{ required: true, message: "请选择供应商", trigger: "change" }],
  model: [{ required: true, message: "请输入模型名称", trigger: "blur" }],
  context_length_k: [
    {
      required: true,
      type: "number",
      validator: (_, v) => typeof v === "number" && Number.isFinite(v) && v >= 1,
      message: "请填写有效的上下文长度（≥ 1K）",
      trigger: ["blur", "change"],
    },
  ],
};

const providerOptions = [
  { label: "OpenAI", value: "openai" },
  { label: "DeepSeek", value: "deepseek" },
  { label: "通义千问 (Qwen)", value: "qwen" },
  { label: "Ollama (本地)", value: "ollama" },
  { label: "自定义", value: "custom" },
];

const providerModels: Record<string, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
  qwen: ["qwen-turbo", "qwen-plus", "qwen-max", "qwen-long"],
  ollama: ["llama3", "qwen2", "mistral", "codellama"],
  custom: [],
};

const providerBaseUrls: Record<string, string> = {
  openai: "https://api.openai.com/v1",
  deepseek: "https://api.deepseek.com",
  qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  ollama: "http://localhost:11434/v1",
  custom: "",
};

const modelSuggestions = computed(() =>
  (providerModels[form.provider] || []).map((m) => ({ label: m, value: m })),
);

const defaultBaseUrl = computed(() => providerBaseUrls[form.provider] || "https://...");

function onProviderChange() {
  const models = providerModels[form.provider];
  if (models?.length && !models.includes(form.model)) {
    form.model = models[0];
  }
  form.base_url = "";
}

function onContextLengthChange(val: number | null) {
  if (val == null || !Number.isFinite(val)) {
    form.context_length_k = 1;
  } else {
    // 不设上限，仅取正整数（避免负数/小数）
    form.context_length_k = Math.max(1, Math.round(val));
  }
}

watch(
  () => props.visible,
  (val) => {
    if (!val) return;
    if (props.editConfig) {
      form.name = props.editConfig.name;
      form.provider = props.editConfig.provider;
      form.model = props.editConfig.model;
      form.api_key = "";
      form.base_url = props.editConfig.base_url || "";
      form.context_length_k = Math.max(
        1,
        Math.round(props.editConfig.max_tokens / 1024),
      );
      form.is_default = props.editConfig.is_default;
    } else {
      form.name = "";
      form.provider = "deepseek";
      form.model = "deepseek-chat";
      form.api_key = "";
      form.base_url = "";
      form.context_length_k = 32;
      form.is_default = false;
    }
  },
);

async function handleTest() {
  testing.value = true;
  try {
    // 编辑模式下若用户没修改 API Key，使用 saved test 接口（后端用 DB 里加密的原始 key）
    const useSaved = isEdit.value && !!props.editConfig && !form.api_key;
    const res = useSaved
      ? await testSavedConfigApi(props.editConfig!.id)
      : await testNewConfigApi({
          provider: form.provider,
          model: form.model,
          api_key: form.api_key || undefined,
          base_url: form.base_url || undefined,
        });
    if (res.success && res.data.success) {
      message.success(
        `${res.data.message}${res.data.response_time_ms ? ` (${res.data.response_time_ms}ms)` : ""}`,
      );
    } else {
      message.warning(res.data?.message || "连接测试失败");
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "";
    message.error(msg || "测试请求失败");
  } finally {
    testing.value = false;
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
    const payload = {
      name: form.name,
      provider: form.provider,
      model: form.model,
      api_key: form.api_key || undefined,
      base_url: form.base_url || undefined,
      max_tokens: Math.max(1, form.context_length_k * 1024),
      is_default: form.is_default,
    };

    if (isEdit.value && props.editConfig) {
      const res = await updateLLMConfigApi(props.editConfig.id, payload);
      if (res.success) {
        message.success("配置已更新");
        emit("update:visible", false);
        emit("saved");
      }
    } else {
      const res = await createLLMConfigApi(payload);
      if (res.success) {
        message.success("配置已创建");
        emit("update:visible", false);
        emit("saved");
      }
    }
  } catch {
    message.error(isEdit.value ? "更新失败" : "创建失败");
  } finally {
    submitting.value = false;
  }
}
</script>
