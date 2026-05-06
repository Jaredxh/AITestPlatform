<template>
  <div class="app-empty" :class="`app-empty--${size}`">
    <div class="app-empty__illustration">
      <slot name="icon">
        <span :class="icon || 'i-carbon-document-blank'" />
      </slot>
    </div>
    <div class="app-empty__title">{{ title }}</div>
    <p v-if="description" class="app-empty__description">{{ description }}</p>
    <div v-if="$slots.actions" class="app-empty__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string;
    description?: string;
    icon?: string;
    size?: "small" | "default" | "large";
  }>(),
  {
    title: "暂无数据",
    size: "default",
  },
);
</script>

<style scoped>
.app-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 48px 16px;
  color: var(--text-secondary);
}

.app-empty--small {
  padding: 28px 12px;
}

.app-empty--large {
  padding: 80px 16px;
}

.app-empty__illustration {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: var(--brand-gradient-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  color: var(--brand-primary);
  margin-bottom: 16px;
  border: 1px solid var(--border-subtle);
}

.app-empty--small .app-empty__illustration {
  width: 56px;
  height: 56px;
  font-size: 24px;
  margin-bottom: 12px;
}

.app-empty__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.app-empty__description {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
  max-width: 320px;
}

.app-empty__actions {
  margin-top: 20px;
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
