<template>
  <section class="cc-section">
    <header class="cc-section__head">
      <span class="i-carbon-cloud-services text-emerald-500" />
      <span>环境</span>
      <n-tag
        size="tiny"
        :bordered="false"
        :type="riskTagType"
        class="ml-1"
        :title="env.risk_reason || ''"
      >
        {{ riskLabel }}
      </n-tag>
    </header>
    <div class="cc-env">
      <div class="cc-env__name">{{ env.name }}</div>
      <div class="cc-env__url">{{ env.base_url }}</div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NTag } from "naive-ui";
import type { EnvironmentSummary } from "../types";

const props = defineProps<{ env: EnvironmentSummary }>();

// M1：M2 task 13.5 接通 risk_level 后徽章颜色 / strict 强度全部按真实字段。
// 这里只做色阶分级；high → 红，medium → 橙，low → 绿。
const riskTagType = computed<"success" | "warning" | "error">(() => {
  switch (props.env.risk_level) {
    case "high":
      return "error";
    case "medium":
      return "warning";
    default:
      return "success";
  }
});
const riskLabel = computed(() => {
  switch (props.env.risk_level) {
    case "high":
      return "高风险";
    case "medium":
      return "中风险";
    default:
      return "低风险";
  }
});
</script>

<style scoped>
.cc-section {
  padding: 8px 0;
  border-bottom: 1px dashed var(--border-subtle);
}
.cc-section__head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}
.cc-env__name {
  font-size: 13px;
  font-weight: 500;
}
.cc-env__url {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono, ui-monospace);
  word-break: break-all;
}
</style>
