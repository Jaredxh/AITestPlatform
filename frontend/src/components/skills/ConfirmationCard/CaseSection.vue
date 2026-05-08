<template>
  <section class="cc-section">
    <header class="cc-section__head">
      <span class="i-carbon-task text-blue-500" />
      <span>用例（{{ cases.length }}）</span>
    </header>
    <ul class="cc-cases">
      <li v-for="(c, idx) in cases" :key="c.id" class="cc-case">
        <span class="cc-case__no">TC-{{ String(c.case_no).padStart(4, "0") }}</span>
        <span class="cc-case__title">{{ c.title }}</span>
        <span class="cc-case__order" v-if="cases.length > 1">#{{ idx + 1 }}</span>
        <n-tag
          v-for="hit in c.matched_via"
          :key="hit"
          size="tiny"
          :bordered="false"
          class="cc-case__hit"
        >
          {{ matchLabel(hit) }}
        </n-tag>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { NTag } from "naive-ui";
import type { CaseSummary } from "../types";

defineProps<{ cases: CaseSummary[] }>();

const labels: Record<string, string> = {
  id_exact: "编号匹配",
  title_fulltext: "标题命中",
  tag_match: "标签命中",
  step_content: "步骤命中",
  recent_fallback: "最近用例",
};
function matchLabel(hit: string) {
  return labels[hit] || hit;
}
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
.cc-cases {
  margin: 0;
  padding: 0;
  list-style: none;
}
.cc-case {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.cc-case__no {
  font-family: var(--font-mono, ui-monospace);
  color: var(--text-tertiary);
  font-size: 11px;
  flex-shrink: 0;
}
.cc-case__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cc-case__order {
  color: var(--text-tertiary);
  font-size: 11px;
}
.cc-case__hit {
  flex-shrink: 0;
}
</style>
