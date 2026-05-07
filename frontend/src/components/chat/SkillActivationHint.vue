<template>
  <transition-group name="hint" tag="div" class="skill-hint-stack">
    <div
      v-for="hint in visible"
      :key="hint.id"
      class="skill-hint"
      :class="`skill-hint--${hint.event.activation_reason}`"
    >
      <span class="skill-hint__icon" :class="iconClass(hint.event.activation_reason)" />
      <div class="skill-hint__body">
        <div class="skill-hint__title">
          已自动激活：<strong>{{ hint.event.name }}</strong>
        </div>
        <div class="skill-hint__sub">
          {{ reasonLabel(hint.event.activation_reason) }}
          <template v-if="hint.event.matched_trigger">
            （命中：{{ hint.event.matched_trigger }}）
          </template>
        </div>
      </div>
      <button
        class="skill-hint__close"
        type="button"
        aria-label="关闭"
        @click="dismiss(hint.id)"
      >
        <span class="i-carbon-close" />
      </button>
    </div>
  </transition-group>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  type SkillActivatedEvent,
  SKILL_ACTIVATION_REASON_LABEL,
  type SkillActivationReason,
} from "@/services/skills";

interface HintEntry {
  id: string;
  event: SkillActivatedEvent;
  expiresAt: number;
}

const props = defineProps<{
  /** 来自父组件的最新 skill_activated 事件；每次变更触发推入栈。 */
  event: SkillActivatedEvent | null;
  /** 可选：每个 banner 自动消失时长（ms），默认 5000。 */
  ttlMs?: number;
}>();

const visible = ref<HintEntry[]>([]);
const timers = new Map<string, number>();

watch(
  () => props.event,
  (ev) => {
    if (!ev) return;
    const id = `${ev.skill_id}-${Date.now()}`;
    const ttl = props.ttlMs ?? 5000;
    const entry: HintEntry = { id, event: ev, expiresAt: Date.now() + ttl };
    visible.value.push(entry);
    const handle = window.setTimeout(() => dismiss(id), ttl);
    timers.set(id, handle);
  },
);

function dismiss(id: string) {
  visible.value = visible.value.filter((e) => e.id !== id);
  const handle = timers.get(id);
  if (handle) {
    window.clearTimeout(handle);
    timers.delete(id);
  }
}

function reasonLabel(reason: SkillActivationReason): string {
  return SKILL_ACTIVATION_REASON_LABEL[reason] || reason;
}

function iconClass(reason: SkillActivationReason): string {
  switch (reason) {
    case "always":
      return "i-carbon-pin";
    case "manual":
      return "i-carbon-user-favorite";
    case "trigger_match":
      return "i-carbon-flash";
    case "agent_callable":
      return "i-carbon-bot";
    default:
      return "i-carbon-skill-level";
  }
}
</script>

<style scoped>
.skill-hint-stack {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 6px;
  pointer-events: none;
}

.skill-hint {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.12);
  min-width: 320px;
  max-width: 480px;
}

.skill-hint--trigger_match {
  border-color: rgba(16, 185, 129, 0.45);
  background: linear-gradient(180deg, rgba(16, 185, 129, 0.08), var(--bg-card));
}

.skill-hint--manual {
  border-color: rgba(99, 102, 241, 0.45);
  background: linear-gradient(180deg, rgba(99, 102, 241, 0.08), var(--bg-card));
}

.skill-hint--always {
  border-color: rgba(245, 158, 11, 0.45);
  background: linear-gradient(180deg, rgba(245, 158, 11, 0.08), var(--bg-card));
}

.skill-hint__icon {
  font-size: 20px;
  color: var(--brand-primary);
  flex-shrink: 0;
  margin-top: 2px;
}

.skill-hint__body {
  flex: 1;
  min-width: 0;
}

.skill-hint__title {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.4;
}

.skill-hint__title strong {
  font-weight: 600;
  color: var(--brand-primary);
  margin-left: 2px;
}

.skill-hint__sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
  line-height: 1.4;
}

.skill-hint__close {
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-tertiary);
  padding: 2px;
  flex-shrink: 0;
}

.skill-hint__close:hover {
  color: var(--text-primary);
}

.hint-enter-active,
.hint-leave-active {
  transition:
    opacity 240ms ease,
    transform 240ms ease;
}

.hint-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.hint-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
