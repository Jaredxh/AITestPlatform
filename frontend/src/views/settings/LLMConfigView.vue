<template>
  <div>
    <page-header title="LLM 配置管理" subtitle="集中管理对接的大模型供应商、API 密钥与默认参数" icon="i-carbon-machine-learning-model">
      <template #extra>
        <n-button type="primary" @click="openCreate">
          <template #icon><span class="i-carbon-add" /></template>
          添加配置
        </n-button>
      </template>
    </page-header>

    <page-container surface>
      <n-spin :show="loading">
        <n-data-table
          v-if="configs.length > 0"
          :columns="columns"
          :data="configs"
          :row-key="(row: LLMConfigInfo) => row.id"
          :bordered="false"
        />
        <app-empty
          v-else-if="!loading"
          icon="i-carbon-machine-learning-model"
          title="还没有 LLM 配置"
          description="添加 OpenAI / DeepSeek / 通义千问 / Ollama 等供应商的接入信息，开始使用 AI 能力"
        >
          <template #actions>
            <n-button type="primary" @click="openCreate">
              <template #icon><span class="i-carbon-add" /></template>
              添加第一个配置
            </n-button>
          </template>
        </app-empty>
      </n-spin>
    </page-container>

    <l-l-m-config-form
      v-model:visible="formVisible"
      :edit-config="editingConfig"
      @saved="fetchConfigs"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, h } from "vue";
import {
  NButton,
  NDataTable,
  NPopconfirm,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import {
  getLLMConfigsApi,
  deleteLLMConfigApi,
  testSavedConfigApi,
} from "@/services/llm";
import type { LLMConfigInfo } from "@/services/llm";
import LLMConfigForm from "@/components/settings/LLMConfigForm.vue";
import PageHeader from "@/components/common/PageHeader.vue";
import PageContainer from "@/components/common/PageContainer.vue";
import AppEmpty from "@/components/common/AppEmpty.vue";

const message = useMessage();
const loading = ref(false);
const configs = ref<LLMConfigInfo[]>([]);
const formVisible = ref(false);
const editingConfig = ref<LLMConfigInfo | null>(null);
const testingId = ref<string | null>(null);

const providerLabelMap: Record<string, string> = {
  openai: "OpenAI",
  deepseek: "DeepSeek",
  qwen: "通义千问",
  ollama: "Ollama",
  custom: "自定义",
};

const columns: DataTableColumns<LLMConfigInfo> = [
  {
    title: "名称",
    key: "name",
    width: 180,
    render(row) {
      return h("div", { class: "flex items-center gap-2" }, [
        h("span", { class: "font-medium" }, row.name),
        row.is_default
          ? h(NTag, { size: "tiny", type: "success", bordered: false }, () => "默认")
          : null,
      ]);
    },
  },
  {
    title: "供应商",
    key: "provider",
    width: 120,
    render(row) {
      return h(NTag, { size: "small", bordered: false }, () => providerLabelMap[row.provider] || row.provider);
    },
  },
  { title: "模型", key: "model", width: 180 },
  {
    title: "API Key",
    key: "has_api_key",
    width: 100,
    align: "center",
    render(row) {
      return h(
        NTag,
        { size: "small", type: row.has_api_key ? "success" : "warning", bordered: false },
        () => (row.has_api_key ? "已配置" : "未配置"),
      );
    },
  },
  {
    title: "上下文长度",
    key: "max_tokens",
    width: 120,
    align: "center",
    render(row) {
      const k = Math.max(1, Math.round(row.max_tokens / 1024));
      return h(NTag, { size: "small", bordered: false, type: "info" }, () => `${k}K`);
    },
  },
  {
    title: "操作",
    key: "actions",
    width: 240,
    align: "right",
    fixed: "right",
    render(row) {
      return h(NSpace, { size: 0, justify: "end" }, () => [
        h(
          NButton,
          {
            size: "small",
            quaternary: true,
            loading: testingId.value === row.id,
            onClick: () => handleTest(row),
          },
          { default: () => "测试", icon: () => h("span", { class: "i-carbon-play" }) },
        ),
        h(
          NButton,
          { size: "small", quaternary: true, type: "info", onClick: () => openEdit(row) },
          { default: () => "编辑", icon: () => h("span", { class: "i-carbon-edit" }) },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(row.id) },
          {
            trigger: () => h(NButton, { size: "small", quaternary: true, type: "error" }, {
              default: () => "删除",
              icon: () => h("span", { class: "i-carbon-trash-can" }),
            }),
            default: () => `确认删除配置「${row.name}」？`,
          },
        ),
      ]);
    },
  },
];

async function fetchConfigs() {
  loading.value = true;
  try {
    const res = await getLLMConfigsApi();
    if (res.success) {
      configs.value = res.data;
    }
  } catch {
    message.error("获取配置列表失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingConfig.value = null;
  formVisible.value = true;
}

function openEdit(config: LLMConfigInfo) {
  editingConfig.value = config;
  formVisible.value = true;
}

async function handleTest(config: LLMConfigInfo) {
  testingId.value = config.id;
  try {
    const res = await testSavedConfigApi(config.id);
    if (res.success && res.data.success) {
      message.success(
        `${res.data.message}${res.data.response_time_ms ? ` (${res.data.response_time_ms}ms)` : ""}`,
      );
    } else {
      message.warning(res.data?.message || "连接测试失败");
    }
  } catch {
    message.error("测试请求失败");
  } finally {
    testingId.value = null;
  }
}

async function handleDelete(configId: string) {
  try {
    const res = await deleteLLMConfigApi(configId);
    if (res.success) {
      message.success("配置已删除");
      fetchConfigs();
    }
  } catch {
    message.error("删除失败");
  }
}

onMounted(fetchConfigs);
</script>
