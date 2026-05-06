<template>
  <!-- 🟠 数据失败原因卡片（Task 10.4）。
       业务语义：列出本用例所有 ``platform_mark_data_failure`` 调用——
       这些条目意味着 AI 已经判定"数据环境不可用"，整张用例会被评为
       data_failure，不计入业务通过率分母。

       数据源：
       - ``case_result.data_failures: list[{key, reason}]``
       - 步骤号关联同 SynthesizedDataCard：扫描 step.tool_calls 中
         ``raw_name === "platform_mark_data_failure"`` 且 ``arguments.key``
         匹配的步骤。

       UI 上下文：失败原因往往较长（AI 写的中文段落），所以 reason 用
       折行展开 + 等宽字体保留换行。 -->
  <div class="failure-card">
    <div class="failure-card__head">
      <span class="failure-card__head-icon">🟠</span>
      <strong class="failure-card__title">数据失败原因</strong>
      <span class="failure-card__count">共 {{ items.length }} 项</span>
      <span class="failure-card__hint">
        AI 已判定数据环境不可用 → 本用例评为 <code>data_failure</code>，不计入业务通过率
      </span>
    </div>

    <div class="failure-card__list">
      <div
        v-for="(item, idx) in items"
        :key="`${item.key}-${idx}`"
        class="failure-card__item"
      >
        <div class="failure-card__item-row">
          <code class="failure-card__key">{{ item.key }}</code>
          <span class="failure-card__steps">
            <template v-if="stepsByKey.get(item.key)?.length">
              触发于
              <button
                v-for="sn in stepsByKey.get(item.key)"
                :key="`${item.key}-${sn}`"
                type="button"
                class="failure-card__step-link"
                @click="$emit('step-click', sn)"
              >
                步骤 {{ sn }}
              </button>
            </template>
            <template v-else>
              <span class="failure-card__no-step">未关联到具体步骤</span>
            </template>
          </span>
        </div>
        <div class="failure-card__reason">
          <span class="failure-card__reason-label">原因：</span>
          <span class="failure-card__reason-text">{{ item.reason || "（未提供原因）" }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

export interface FailureItem {
  key: string;
  reason: string;
}

export interface StepLite {
  step_number: number;
  tool_calls?: unknown[];
}

const props = defineProps<{
  /** ``case_result.data_failures`` */
  items: FailureItem[];
  /** 用于步骤号反查 */
  steps?: StepLite[];
}>();

defineEmits<{
  "step-click": [stepNumber: number];
}>();

const stepsByKey = computed<Map<string, number[]>>(() => {
  const m = new Map<string, number[]>();
  if (!props.steps) return m;
  for (const step of props.steps) {
    const calls = (step.tool_calls ?? []) as Array<Record<string, unknown>>;
    for (const c of calls) {
      const rawName = String(c.raw_name ?? c.name ?? "");
      if (!rawName.endsWith("platform_mark_data_failure")) continue;
      const args = (c.arguments ?? {}) as Record<string, unknown>;
      const k = typeof args.key === "string" ? args.key.trim() : "";
      if (!k) continue;
      const arr = m.get(k) ?? [];
      if (!arr.includes(step.step_number)) {
        arr.push(step.step_number);
        arr.sort((a, b) => a - b);
      }
      m.set(k, arr);
    }
  }
  return m;
});
</script>

<style scoped>
.failure-card {
  border: 1px solid rgba(239, 68, 68, 0.25);
  background: rgba(239, 68, 68, 0.06);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.failure-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.failure-card__head-icon {
  font-size: 16px;
}

.failure-card__title {
  color: #b91c1c;
  font-size: 13px;
}

.failure-card__count {
  font-size: 12px;
  color: #b91c1c;
  background: rgba(239, 68, 68, 0.16);
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 600;
}

.failure-card__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: auto;
}

.failure-card__hint code {
  background: var(--bg-page-soft);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
  color: #b91c1c;
}

.failure-card__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.failure-card__item {
  background: var(--bg-card);
  border: 1px solid rgba(239, 68, 68, 0.18);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.failure-card__item-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.failure-card__key {
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-weight: 600;
  color: var(--text-primary);
}

.failure-card__steps {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.failure-card__step-link {
  background: rgba(14, 165, 233, 0.08);
  color: #0369a1;
  border: 1px solid rgba(14, 165, 233, 0.25);
  border-radius: 999px;
  padding: 0 6px;
  font-size: 11px;
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--easing-standard);
  font: inherit;
  line-height: 16px;
}

.failure-card__step-link:hover {
  background: rgba(14, 165, 233, 0.16);
}

.failure-card__no-step {
  font-style: italic;
  color: var(--text-tertiary);
}

.failure-card__reason {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.6;
}

.failure-card__reason-label {
  color: var(--text-tertiary);
  font-size: 11px;
  flex-shrink: 0;
  margin-top: 2px;
}

.failure-card__reason-text {
  white-space: pre-wrap;
  word-break: break-word;
  flex: 1;
  min-width: 0;
}
</style>
