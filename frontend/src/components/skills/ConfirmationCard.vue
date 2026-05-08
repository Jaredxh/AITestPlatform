<template>
  <div class="confirm-card" :class="`confirm-card--${plan.confirmation_strength}`">
    <header class="confirm-card__head">
      <span class="i-carbon-task-approved text-blue-500" />
      <span class="confirm-card__title">AI 提议执行计划</span>
      <n-tag size="tiny" :bordered="false" :type="strengthTagType">
        {{ strengthLabel }}
      </n-tag>
      <span class="confirm-card__expire" v-if="expireText">
        ⏱ {{ expireText }}
      </span>
    </header>

    <case-section :cases="plan.cases" />
    <environment-section :env="plan.environment" />
    <test-data-section :preview="plan.test_data_preview" />

    <section class="cc-section confirm-card__llm">
      <span class="i-carbon-machine-learning-model mr-1 text-amber-500" />
      <span>{{ plan.llm_provider.name }}</span>
      <span class="confirm-card__llm-model">{{ plan.llm_provider.model }}</span>
      <span class="confirm-card__est ml-auto">
        预计 ~{{ formatSeconds(plan.estimated_duration_seconds) }}
      </span>
    </section>

    <strict-confirm-dialog
      v-if="plan.confirmation_strength === 'strict'"
      :payload="plan.confirmation_payload"
      @ready="(v) => (strictAcked = v)"
    />

    <footer class="confirm-card__actions">
      <n-button
        v-if="state === 'idle'"
        size="small"
        @click="handleCancel"
        :disabled="busy"
      >
        取消
      </n-button>
      <n-button
        v-if="state === 'idle'"
        size="small"
        type="primary"
        :loading="busy"
        :disabled="!canConfirm"
        @click="handleConfirm"
      >
        {{ plan.confirmation_strength === "strict" ? "确认执行（高风险）" : "确认执行" }}
      </n-button>
      <span v-else-if="state === 'cancelled'" class="confirm-card__hint">
        已取消；如需执行请重新让 AI 生成计划。
      </span>
      <span v-else-if="state === 'error'" class="confirm-card__hint confirm-card__hint--error">
        {{ errorMsg || "派发失败，请重试" }}
      </span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from "vue";
import { NButton, NTag, useMessage } from "naive-ui";
import CaseSection from "./ConfirmationCard/CaseSection.vue";
import EnvironmentSection from "./ConfirmationCard/EnvironmentSection.vue";
import TestDataSection from "./ConfirmationCard/TestDataSection.vue";
import StrictConfirmDialog from "./ConfirmationCard/StrictConfirmDialog.vue";
import type { ExecutionPlanCard } from "./types";

const props = defineProps<{
  plan: ExecutionPlanCard;
  /** 当前会话 id；派发时回填 triggered_chat_session_id。 */
  sessionId: string;
  /** 当前消息 id；派发成功后用于"原地变身"为 task_badge。 */
  messageId: string;
}>();

const emit = defineEmits<{
  (e: "confirm", payload: { taskId: string; plan: ExecutionPlanCard; messageId: string }): void;
  (e: "cancel"): void;
}>();

const message = useMessage();
const state = ref<"idle" | "confirming" | "confirmed" | "cancelled" | "error">("idle");
const busy = computed(() => state.value === "confirming");
const errorMsg = ref<string>("");
const strictAcked = ref(false);

const canConfirm = computed(() => {
  if (props.plan.confirmation_strength === "strict") {
    return strictAcked.value;
  }
  return true;
});

const strengthLabel = computed(() => {
  switch (props.plan.confirmation_strength) {
    case "strict":
      return "强确认";
    case "soft":
      return "确认";
    default:
      return "建议";
  }
});
const strengthTagType = computed<"error" | "warning" | "default">(() => {
  switch (props.plan.confirmation_strength) {
    case "strict":
      return "error";
    case "soft":
      return "warning";
    default:
      return "default";
  }
});

// ─── 倒计时（plan TTL 10 分钟） ─────────────────────────────────────
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

const expireText = computed(() => {
  if (!props.plan.expires_at) return "";
  const t = new Date(props.plan.expires_at).getTime();
  const remain = Math.max(0, Math.floor((t - now.value) / 1000));
  if (remain <= 0) return "已过期，请重新生成";
  const m = Math.floor(remain / 60);
  const s = remain % 60;
  return `${m}:${String(s).padStart(2, "0")} 后失效`;
});

function formatSeconds(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function handleCancel() {
  state.value = "cancelled";
  emit("cancel");
}

async function handleConfirm() {
  if (state.value !== "idle" || !canConfirm.value) return;
  state.value = "confirming";
  errorMsg.value = "";
  try {
    const { confirmExecutionPlanApi } = await import("@/services/uiAutomation");
    const resp = await confirmExecutionPlanApi(props.plan.project_id, {
      plan_id: props.plan.plan_id,
      triggered_chat_session_id: props.sessionId,
    });
    if (!resp.success || !resp.data?.id) {
      throw new Error(resp.message || "派发失败");
    }
    state.value = "confirmed";
    emit("confirm", {
      taskId: resp.data.id,
      plan: props.plan,
      messageId: props.messageId,
    });
  } catch (err: unknown) {
    state.value = "error";
    errorMsg.value = err instanceof Error ? err.message : "派发失败";
    message.error(errorMsg.value);
  }
}
</script>

<style scoped>
.confirm-card {
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 8px 14px 10px;
  background: var(--bg-card);
}
.confirm-card--strict {
  border-color: color-mix(in srgb, var(--brand-error, #d03050) 35%, transparent);
  background: color-mix(in srgb, var(--brand-error, #d03050) 4%, var(--bg-card));
}
.confirm-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-subtle);
  margin-bottom: 6px;
}
.confirm-card__expire {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
}
.confirm-card__llm {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  padding: 6px 0;
}
.confirm-card__llm-model {
  color: var(--text-tertiary);
  font-family: var(--font-mono, ui-monospace);
  font-size: 11px;
}
.confirm-card__est {
  font-size: 11px;
  color: var(--text-tertiary);
}
.confirm-card__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border-subtle);
}
.confirm-card__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin: 0;
}
.confirm-card__hint--error {
  color: var(--brand-error, #d03050);
}
</style>
