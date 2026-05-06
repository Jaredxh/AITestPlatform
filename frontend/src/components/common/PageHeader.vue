<template>
  <div class="page-header" :class="{ 'page-header--bordered': bordered }">
    <app-breadcrumb v-if="breadcrumbs && breadcrumbs.length" :items="breadcrumbs" class="mb-2" />

    <div class="page-header__row">
      <div class="page-header__title-block">
        <button
          v-if="back"
          class="page-header__back"
          type="button"
          aria-label="返回"
          @click="$emit('back')"
        >
          <span class="i-carbon-arrow-left" />
        </button>
        <slot name="icon">
          <span v-if="icon" class="page-header__icon" :class="icon" />
        </slot>
        <div class="page-header__title-text">
          <h1 class="page-header__title">{{ title }}</h1>
          <p v-if="subtitle || $slots.subtitle" class="page-header__subtitle">
            <slot name="subtitle">{{ subtitle }}</slot>
          </p>
        </div>
        <slot name="title-extra" />
      </div>
      <div v-if="$slots.extra" class="page-header__actions">
        <slot name="extra" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppBreadcrumb from "./AppBreadcrumb.vue";
import type { BreadcrumbItem } from "./AppBreadcrumb.vue";

defineProps<{
  title: string;
  subtitle?: string;
  icon?: string;
  back?: boolean;
  bordered?: boolean;
  breadcrumbs?: BreadcrumbItem[];
}>();

defineEmits<{
  back: [];
}>();
</script>

<style scoped>
.page-header {
  margin-bottom: 20px;
}

.page-header--bordered {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-subtle);
}

.page-header__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.page-header__title-block {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.page-header__back {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  background-color: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    border-color var(--duration-fast) var(--easing-standard),
    color var(--duration-fast) var(--easing-standard);
}

.page-header__back:hover {
  background-color: var(--bg-active);
  border-color: var(--brand-primary-border);
  color: var(--brand-primary);
}

.page-header__icon {
  font-size: 22px;
  color: var(--brand-primary);
}

.page-header__title-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.page-header__title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.2px;
  line-height: 1.3;
}

.page-header__subtitle {
  margin: 0;
  font-size: 13px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
</style>
