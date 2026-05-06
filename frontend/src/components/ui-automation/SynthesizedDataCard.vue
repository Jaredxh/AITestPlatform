<template>
  <!-- 🟡 自造数据卡片（Task 10.4）。
       业务语义：列出本用例所有 ``platform_synthesize_data`` 调用产生的"假数据"，
       并把每一条与触发它的 step_number 关联起来——便于排查"哪一步缺料 → AI
       帮你编了什么 → 假值是什么"。

       数据源：
       - ``case_result.synthesized_data: list[{key, value, source, hint?}]``
         （``finalize_case`` 一次性写入；不含步骤号）
       - 通过 ``case.steps[*].tool_calls`` 反向匹配 ``raw_name ===
         "platform_synthesize_data"`` 且 ``arguments.key === item.key`` 的步骤号

       value 显示策略：默认折叠到 80 字内 + "查看完整"按钮；防止 dataset
       生成的大块字符串撑爆 UI。 -->
  <div class="synth-card">
    <div class="synth-card__head">
      <span class="synth-card__head-icon">🟡</span>
      <strong class="synth-card__title">AI 自造数据</strong>
      <span class="synth-card__count">共 {{ items.length }} 项</span>
      <span class="synth-card__hint">
        本用例至少调用了一次 <code>platform_synthesize_data</code>，请人工复核
      </span>
    </div>

    <div class="synth-card__list">
      <div
        v-for="(item, idx) in items"
        :key="`${item.key}-${idx}`"
        class="synth-card__item"
      >
        <div class="synth-card__item-row">
          <code class="synth-card__key">{{ item.key }}</code>
          <span class="synth-card__source">
            <span class="i-carbon-flash" />
            {{ item.source || "未知来源" }}
          </span>
          <span v-if="item.hint" class="synth-card__hint-pill">
            hint: {{ item.hint }}
          </span>
          <span class="synth-card__steps">
            <template v-if="stepsByKey.get(item.key)?.length">
              触发于
              <button
                v-for="sn in stepsByKey.get(item.key)"
                :key="`${item.key}-${sn}`"
                type="button"
                class="synth-card__step-link"
                @click="$emit('step-click', sn)"
              >
                步骤 {{ sn }}
              </button>
            </template>
            <template v-else>
              <span class="synth-card__no-step">未关联到具体步骤</span>
            </template>
          </span>
        </div>
        <div class="synth-card__value">
          <span class="synth-card__value-label">值：</span>
          <code v-if="!isExpanded(idx)" class="synth-card__value-text">
            {{ truncate(item.value) }}
          </code>
          <code v-else class="synth-card__value-text synth-card__value-text--full">{{ item.value }}</code>
          <button
            v-if="needsTruncate(item.value)"
            type="button"
            class="synth-card__toggle"
            @click="toggle(idx)"
          >
            {{ isExpanded(idx) ? "收起" : "查看完整" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

export interface SynthesizedItem {
  key: string;
  value: string;
  source?: string | null;
  hint?: string | null;
}

export interface StepLite {
  step_number: number;
  tool_calls?: unknown[];
}

const props = defineProps<{
  /** ``case_result.synthesized_data`` */
  items: SynthesizedItem[];
  /** 该用例的 ``steps``，用于把 key 反查到触发它的步骤号 */
  steps?: StepLite[];
}>();

defineEmits<{
  /** 用户点击"步骤 N"链接 → 父级（详情页）滚动到该步骤 */
  "step-click": [stepNumber: number];
}>();

// ─── key → 触发步骤号集合（按 step_number 升序） ──────────────────────

const stepsByKey = computed<Map<string, number[]>>(() => {
  const m = new Map<string, number[]>();
  if (!props.steps) return m;
  for (const step of props.steps) {
    const calls = (step.tool_calls ?? []) as Array<Record<string, unknown>>;
    for (const c of calls) {
      const rawName = String(c.raw_name ?? c.name ?? "");
      if (!rawName.endsWith("platform_synthesize_data")) continue;
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

// ─── value 截断 + 展开/收起 ──────────────────────────────────────────

const TRUNCATE_AT = 80;

function needsTruncate(v: string | null | undefined): boolean {
  return typeof v === "string" && v.length > TRUNCATE_AT;
}

function truncate(v: string | null | undefined): string {
  if (typeof v !== "string") return "";
  if (v.length <= TRUNCATE_AT) return v;
  return `${v.slice(0, TRUNCATE_AT)}…`;
}

const expanded = ref(new Set<number>());

function isExpanded(idx: number): boolean {
  return expanded.value.has(idx);
}

function toggle(idx: number) {
  const next = new Set(expanded.value);
  if (next.has(idx)) next.delete(idx);
  else next.add(idx);
  expanded.value = next;
}
</script>

<style scoped>
.synth-card {
  border: 1px solid rgba(245, 158, 11, 0.25);
  background: rgba(245, 158, 11, 0.06);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.synth-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.synth-card__head-icon {
  font-size: 16px;
}

.synth-card__title {
  color: #b45309;
  font-size: 13px;
}

.synth-card__count {
  font-size: 12px;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 600;
}

.synth-card__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: auto;
}

.synth-card__hint code {
  background: var(--bg-page-soft);
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
}

.synth-card__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.synth-card__item {
  background: var(--bg-card);
  border: 1px solid rgba(245, 158, 11, 0.18);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.synth-card__item-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.synth-card__key {
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  color: var(--text-primary);
  font-weight: 600;
}

.synth-card__source {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
  padding: 1px 6px;
  border-radius: 999px;
}

.synth-card__hint-pill {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 999px;
}

.synth-card__steps {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.synth-card__step-link {
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

.synth-card__step-link:hover {
  background: rgba(14, 165, 233, 0.16);
}

.synth-card__no-step {
  font-style: italic;
  color: var(--text-tertiary);
}

.synth-card__value {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-secondary);
}

.synth-card__value-label {
  color: var(--text-tertiary);
  font-size: 11px;
  flex-shrink: 0;
  margin-top: 2px;
}

.synth-card__value-text {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  background: var(--bg-page-soft);
  padding: 2px 6px;
  border-radius: 4px;
  word-break: break-all;
  max-width: 100%;
}

.synth-card__value-text--full {
  white-space: pre-wrap;
}

.synth-card__toggle {
  font-size: 11px;
  background: transparent;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  padding: 0 6px;
  cursor: pointer;
  font: inherit;
  line-height: 18px;
}

.synth-card__toggle:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}
</style>
