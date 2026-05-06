<template>
  <section class="page-container" :class="containerClass">
    <slot />
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  surface?: boolean;
  padded?: boolean;
}>();

const containerClass = computed(() => ({
  "page-container--surface": props.surface,
  "page-container--padded": props.padded,
}));
</script>

<style scoped>
.page-container {
  width: 100%;
}

.page-container--surface {
  background-color: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.page-container--padded {
  padding: 18px 22px;
}
</style>
