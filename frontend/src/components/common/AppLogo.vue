<template>
  <div class="app-logo" :class="{ 'app-logo--collapsed': collapsed }">
    <div class="app-logo__mark">
      <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
        <defs>
          <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#fff" stop-opacity="0.95" />
            <stop offset="100%" stop-color="#fff" stop-opacity="0.6" />
          </linearGradient>
        </defs>
        <path
          d="M16 3 L27 9 V23 L16 29 L5 23 V9 Z"
          fill="url(#lg)"
          stroke="rgba(255,255,255,0.4)"
          stroke-width="0.6"
        />
        <path
          d="M11 21 L16 11 L21 21 M13 17 H19"
          fill="none"
          stroke="#4338CA"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </div>
    <transition name="fade">
      <div v-if="!collapsed" class="app-logo__text">
        <span class="app-logo__title">AITest</span>
        <span class="app-logo__subtitle">Platform</span>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  collapsed?: boolean;
}>();
</script>

<style scoped>
.app-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  height: var(--layout-header-height);
  user-select: none;
}

.app-logo--collapsed {
  justify-content: center;
  padding: 0;
}

.app-logo__mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--brand-gradient);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow:
    0 6px 14px -4px rgba(79, 70, 229, 0.5),
    inset 0 1px 0 rgba(255, 255, 255, 0.3);
  flex-shrink: 0;
}

.app-logo__text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.app-logo__title {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.2px;
  color: var(--text-primary);
}

.app-logo__subtitle {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 500;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}
</style>
