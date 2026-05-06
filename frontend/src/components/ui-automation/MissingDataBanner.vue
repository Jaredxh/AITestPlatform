<template>
  <!-- 缺料告警条（Task 10.1）。
       v3.0.1 关键约定："缺料只警告不阻断"——默认黄色 alert + AI 兜底说明；
       仅当用户明确勾了"严格模式"时才升级为红色 + 阻断说明。
       检测细节：每个 missing_key 可能在多个用例多个步骤出现，给个 "(N 处)" 摘要。 -->
  <n-alert
    v-if="visible"
    :type="alertType"
    :title="title"
    show-icon
    class="missing-banner"
  >
    <template v-if="strictMode">
      严格模式已开启：未提供 <strong>{{ missingKeys.length }}</strong> 项物料，
      执行将被拒绝。请到「测试物料」展开区手动填值，或取消严格模式让 AI 自造。
    </template>
    <template v-else>
      用例步骤含未提供物料：
      <span
        v-for="(alert, idx) in displayAlerts"
        :key="alert.key"
      >
        <code class="missing-banner__key">{{ alert.key }}</code>
        <span class="missing-banner__count" v-if="alert.detected_in_steps.length > 0">
          ({{ alert.detected_in_steps.length }} 处)
        </span>
        <span v-if="idx < displayAlerts.length - 1">、</span>
      </span>
      <span v-if="extraCount > 0" class="missing-banner__extra">
        ，等共 {{ missingKeys.length }} 项
      </span>
      。AI 将自动生成测试数据；若数据导致用例失败，<strong>会在用例报告中标记为
      data_failure，不会计入业务缺陷</strong>，也不会阻断后续用例。
    </template>
  </n-alert>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NAlert } from "naive-ui";
import type { MissingAlert } from "@/services/uiAutomation";

const props = withDefaults(defineProps<{
  /** missing-check 端点返回的 missing_keys（去重） */
  missingKeys: string[];
  /** 与 missing_keys 等价但带有 "在哪些步骤被引用"——用于显示 (N 处) */
  details?: MissingAlert[];
  /** 严格模式开关：true 时 banner 变红、按钮置灰由调用方控制 */
  strictMode?: boolean;
  /** 单行最多展示前 N 个 key，超出折叠成"等共 N 项" */
  maxInline?: number;
}>(), {
  details: () => [],
  strictMode: false,
  maxInline: 5,
});

const visible = computed(() => props.missingKeys.length > 0);

const alertType = computed<"warning" | "error">(() =>
  props.strictMode ? "error" : "warning",
);

const title = computed(() =>
  props.strictMode
    ? `严格模式：${props.missingKeys.length} 项物料缺失，无法执行`
    : "可继续执行：AI 将自动生成缺失物料",
);

/**
 * 对 details 做"前 N + 兜底"：超长时把 (M 处) 信息保留给可见的，剩下的合并为
 * "等共 N 项"。注意 details 可能比 missing_keys 短（后端只在跨步骤被引用时
 * 才填 detail），所以遍历 missing_keys 而不是 details 才能保证不漏 key。
 */
const displayAlerts = computed<MissingAlert[]>(() => {
  const detailMap = new Map<string, MissingAlert>();
  for (const d of props.details) detailMap.set(d.key, d);
  const all = props.missingKeys.map<MissingAlert>((key) => {
    const found = detailMap.get(key);
    return found ?? {
      key,
      detected_in_steps: [],
      will_synthesize: true,
    };
  });
  return all.slice(0, props.maxInline);
});

const extraCount = computed(() =>
  Math.max(0, props.missingKeys.length - props.maxInline),
);
</script>

<style scoped>
.missing-banner {
  border-radius: var(--radius-md);
}

.missing-banner__key {
  display: inline-block;
  padding: 1px 6px;
  margin: 0 2px;
  background: rgba(245, 158, 11, 0.16);
  color: var(--color-warning, #b45309);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.missing-banner__count {
  margin-left: 2px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.missing-banner__extra {
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
