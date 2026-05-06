<template>
  <n-breadcrumb v-if="items.length > 0" separator="/" class="app-breadcrumb">
    <n-breadcrumb-item v-for="(item, idx) in items" :key="idx">
      <span
        v-if="!item.to || idx === items.length - 1"
        :class="idx === items.length - 1 ? 'app-breadcrumb__current' : ''"
      >
        {{ item.label }}
      </span>
      <router-link v-else :to="item.to" class="app-breadcrumb__link">
        {{ item.label }}
      </router-link>
    </n-breadcrumb-item>
  </n-breadcrumb>
</template>

<script setup lang="ts">
import { NBreadcrumb, NBreadcrumbItem } from "naive-ui";
import type { RouteLocationRaw } from "vue-router";

export interface BreadcrumbItem {
  label: string;
  to?: RouteLocationRaw;
}

defineProps<{
  items: BreadcrumbItem[];
}>();
</script>

<style scoped>
.app-breadcrumb {
  font-size: 13px;
}

.app-breadcrumb__link {
  color: var(--text-secondary);
  text-decoration: none;
  transition: color var(--duration-fast) var(--easing-standard);
}

.app-breadcrumb__link:hover {
  color: var(--brand-primary);
}

.app-breadcrumb__current {
  color: var(--text-primary);
  font-weight: 500;
}
</style>
