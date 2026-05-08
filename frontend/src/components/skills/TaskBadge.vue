<template>
  <div class="task-badge" :class="`task-badge--${normalizedStatus}`">
    <header class="task-badge__head">
      <span class="task-badge__icon" :class="iconClass" />
      <span class="task-badge__title">
        {{ meta.title || "UI 自动化任务" }}
      </span>
      <n-tag size="tiny" :bordered="false" :type="statusTagType">
        {{ statusLabel }}
      </n-tag>
    </header>

    <div class="task-badge__progress" v-if="totalCases > 0">
      <span class="task-badge__progress-text">
        {{ doneCases }} / {{ totalCases }}
      </span>
      <div class="task-badge__progress-bar">
        <div
          class="task-badge__progress-fill"
          :style="{ width: `${progressPercent}%` }"
        />
      </div>
      <span v-if="meta.duration_ms" class="task-badge__duration">
        {{ formatDuration(meta.duration_ms) }}
      </span>
    </div>
    <div v-else class="task-badge__progress-text">已派发，等待开始执行...</div>

    <footer class="task-badge__actions">
      <n-button text size="tiny" @click="goDetail">
        <span class="i-carbon-document mr-1" />任务详情
      </n-button>
      <n-button
        v-if="!terminal"
        text
        size="tiny"
        :loading="refreshing"
        @click="refreshTask"
      >
        <span class="i-carbon-renew mr-1" />刷新状态
      </n-button>
      <n-button
        v-if="errorState"
        text
        size="tiny"
        type="warning"
        @click="refreshTask"
      >
        <span class="i-carbon-warning-alt mr-1" />状态查询失败 [刷新]
      </n-button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { NButton, NTag } from "naive-ui";
import { useRouter } from "vue-router";
import type { TaskBadgeMeta } from "./types";
import { getExecutionApi } from "@/services/uiAutomation";

const props = defineProps<{
  meta: TaskBadgeMeta;
  /** 上层（MessageBubble / useChat）拿到最新态后回写消息 meta_data。 */
  onUpdate?: (patch: Partial<TaskBadgeMeta>) => void;
}>();

const router = useRouter();
const refreshing = ref(false);
const errorState = ref(false);

const TERMINAL = new Set(["completed", "stopped", "failed", "aborted_budget"]);

const normalizedStatus = computed(() => (props.meta.status || "pending").toLowerCase());
const terminal = computed(() => TERMINAL.has(normalizedStatus.value));

const totalCases = computed(() => props.meta.total_cases ?? 0);
const doneCases = computed(
  () =>
    (props.meta.passed_cases ?? 0) +
    (props.meta.failed_cases ?? 0) +
    (props.meta.skipped_cases ?? 0),
);
const progressPercent = computed(() => {
  if (totalCases.value === 0) return 0;
  return Math.min(100, Math.round((doneCases.value / totalCases.value) * 100));
});

const statusLabel = computed(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "已完成";
    case "running":
      return "执行中";
    case "pending":
      return "等待中";
    case "stopped":
      return "已停止";
    case "failed":
      return "失败";
    case "aborted_budget":
      return "预算耗尽";
    default:
      return normalizedStatus.value;
  }
});

const statusTagType = computed<"success" | "info" | "warning" | "error" | "default">(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "success";
    case "running":
      return "info";
    case "stopped":
      return "warning";
    case "failed":
    case "aborted_budget":
      return "error";
    default:
      return "default";
  }
});

const iconClass = computed(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "i-carbon-checkmark-filled text-emerald-500";
    case "running":
      return "i-carbon-circle-dash text-blue-500";
    case "failed":
    case "aborted_budget":
      return "i-carbon-warning-filled text-rose-500";
    case "stopped":
      return "i-carbon-stop-filled-alt text-amber-500";
    default:
      return "i-carbon-time text-gray-500";
  }
});

function formatDuration(ms: number): string {
  if (!ms || ms <= 0) return "—";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function goDetail() {
  router.push({
    name: "UIExecutionDetail",
    params: { id: props.meta.task_id },
  });
}

async function refreshTask() {
  if (refreshing.value) return;
  refreshing.value = true;
  errorState.value = false;
  try {
    const resp = await getExecutionApi(props.meta.task_id);
    if (resp.success && resp.data) {
      props.onUpdate?.({
        status: resp.data.status,
        total_cases: resp.data.total_cases,
        passed_cases: resp.data.passed_cases,
        failed_cases: resp.data.failed_cases,
        skipped_cases: resp.data.skipped_cases,
        duration_ms: resp.data.duration_ms,
      });
    } else {
      errorState.value = true;
    }
  } catch {
    errorState.value = true;
  } finally {
    refreshing.value = false;
  }
}

// 组件挂载即刷新一次：用户离线回来 / 切走再切回，通过这次刷新把状态拉到最新；
// 后续依赖 system-events SSE 增量推送（task_status 事件）保持实时。
onMounted(() => {
  if (!terminal.value) {
    refreshTask();
  }
});
</script>

<style scoped>
.task-badge {
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 10px 14px;
  background: var(--bg-card);
}
.task-badge--running {
  border-color: color-mix(in srgb, #3b82f6 40%, transparent);
}
.task-badge--completed {
  border-color: color-mix(in srgb, #10b981 40%, transparent);
}
.task-badge--failed,
.task-badge--aborted_budget {
  border-color: color-mix(in srgb, #f43f5e 40%, transparent);
}
.task-badge__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.task-badge__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-badge__progress {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.task-badge__progress-text {
  font-family: var(--font-mono, ui-monospace);
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.task-badge__progress-bar {
  flex: 1;
  height: 4px;
  background: var(--bg-hover);
  border-radius: 2px;
  overflow: hidden;
}
.task-badge__progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #8b5cf6);
  transition: width 240ms ease-out;
}
.task-badge__duration {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
}
.task-badge__actions {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  padding-top: 4px;
  border-top: 1px dashed var(--border-subtle);
}
</style>
