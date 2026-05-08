<template>
  <section class="cc-section">
    <header class="cc-section__head">
      <span class="i-carbon-data-base text-violet-500" />
      <span>物料预览（{{ preview.items.length }}）</span>
      <n-tag
        v-if="preview.missing_semantics?.length"
        size="tiny"
        :bordered="false"
        type="warning"
        class="ml-1"
      >
        缺 {{ preview.missing_semantics.length }} 项
      </n-tag>
    </header>

    <div v-if="preview.items.length === 0" class="cc-data__empty">
      未找到匹配的默认物料；M2 阶段会按用例 semantic 自动解析。
    </div>

    <ul v-else class="cc-data">
      <li v-for="item in preview.items" :key="item.key" class="cc-data__item">
        <span class="cc-data__key">{{ item.key }}</span>
        <span class="cc-data__value" :class="{ 'cc-data__value--secret': item.is_secret }">
          {{ item.is_secret ? "●●●●●●" : item.value_preview }}
        </span>
        <span class="cc-data__src" :title="item.source">
          {{ item.source }}
        </span>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { NTag } from "naive-ui";
import type { TestDataPreview } from "../types";

defineProps<{ preview: TestDataPreview }>();
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
.cc-data {
  margin: 0;
  padding: 0;
  list-style: none;
}
.cc-data__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 12px;
}
.cc-data__key {
  font-family: var(--font-mono, ui-monospace);
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 80px;
}
.cc-data__value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}
.cc-data__value--secret {
  color: var(--text-tertiary);
  font-family: var(--font-mono, ui-monospace);
  letter-spacing: 1px;
}
.cc-data__src {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
  max-width: 30%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cc-data__empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 4px 0;
}
</style>
