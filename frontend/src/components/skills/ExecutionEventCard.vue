<template>
  <div class="exec-event" :class="`exec-event--${normalizedStatus}`">
    <header class="exec-event__head">
      <span class="exec-event__icon" :class="iconClass" />
      <span class="exec-event__title">
        {{ result.title || "UI 自动化任务" }} 已完成
      </span>
      <n-tag size="tiny" :bordered="false" :type="statusTagType">
        {{ statusLabel }}
      </n-tag>
    </header>

    <div class="exec-event__summary">
      <span v-if="result.total_cases">
        {{ result.passed_cases ?? 0 }} 通过 / {{ result.failed_cases ?? 0 }} 失败 /
        共 {{ result.total_cases }}
      </span>
      <span v-if="result.duration_ms" class="exec-event__sep">·</span>
      <span v-if="result.duration_ms">耗时 {{ formatDuration(result.duration_ms) }}</span>
    </div>

    <p v-if="result.error_message" class="exec-event__error">
      {{ result.error_message }}
    </p>

    <footer class="exec-event__actions">
      <n-button text size="tiny" @click="openReport">
        <span class="i-carbon-report mr-1" />打开报告
      </n-button>
      <n-button v-if="hasVideo" text size="tiny" @click="openVideo">
        <span class="i-carbon-video mr-1" />查看视频
      </n-button>
      <n-button
        v-if="failedOrAborted"
        text
        size="tiny"
        type="warning"
        @click="diagnoseFailure"
      >
        <span class="i-carbon-flash mr-1" />失败诊断
      </n-button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { NButton, NTag, useMessage } from "naive-ui";
import type { ExecutionEventMeta } from "./types";
import { executionVideoUrl, getExecutionApi } from "@/services/uiAutomation";

const props = defineProps<{ meta: ExecutionEventMeta }>();
const router = useRouter();
const message = useMessage();

const result = computed(() => props.meta.result || {});
const normalizedStatus = computed(
  () => (result.value.status || "").toLowerCase(),
);
const failedOrAborted = computed(
  () =>
    normalizedStatus.value === "failed" ||
    normalizedStatus.value === "aborted_budget" ||
    normalizedStatus.value === "error",
);

const statusLabel = computed(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "已通过";
    case "failed":
      return "失败";
    case "stopped":
      return "已停止";
    case "aborted_budget":
      return "预算耗尽";
    default:
      return result.value.status || "完成";
  }
});

const statusTagType = computed<"success" | "warning" | "error" | "default">(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "success";
    case "stopped":
      return "warning";
    case "failed":
    case "aborted_budget":
    case "error":
      return "error";
    default:
      return "default";
  }
});

const iconClass = computed(() => {
  switch (normalizedStatus.value) {
    case "completed":
      return "i-carbon-checkmark-filled text-emerald-500";
    case "failed":
    case "aborted_budget":
    case "error":
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
  if (sec < 60) return `${sec} 秒`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分`;
}

// 视频可用性按需懒查询：result.has_video 字段在事件 payload 里通常没有，
// 实际有无视频要 GET /ui-executions/{id} 才知道。这里用一个小标志位避免
// 每次渲染都拉一次接口。
const hasVideo = ref(false);
onMounted(async () => {
  if (!props.meta.task_id) return;
  try {
    const resp = await getExecutionApi(props.meta.task_id);
    if (resp.success && resp.data?.has_video) {
      hasVideo.value = true;
    }
  } catch {
    /* swallow — 视频按钮缺失不影响主流程 */
  }
});

function openReport() {
  router.push({
    name: "UIExecutionDetail",
    params: { id: props.meta.task_id },
  });
}

function openVideo() {
  window.open(executionVideoUrl(props.meta.task_id), "_blank", "noopener");
}

function diagnoseFailure() {
  // M1 占位：失败诊断 skill 在 task 13.7 接通；当前先 toast 提示用户去任务详情看
  // 失败步骤截图与日志。M3 启用后这里会触发 LLM agent 分析。
  message.info("失败诊断技能将在三期 M3 上线，请先到任务详情查看失败步骤");
}
</script>

<style scoped>
.exec-event {
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 10px 14px;
  background: var(--bg-card);
}
.exec-event--completed {
  border-color: color-mix(in srgb, #10b981 40%, transparent);
  background: color-mix(in srgb, #10b981 4%, var(--bg-card));
}
.exec-event--failed,
.exec-event--aborted_budget {
  border-color: color-mix(in srgb, #f43f5e 40%, transparent);
  background: color-mix(in srgb, #f43f5e 4%, var(--bg-card));
}
.exec-event__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}
.exec-event__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exec-event__summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.exec-event__sep {
  color: var(--text-tertiary);
}
.exec-event__error {
  font-size: 12px;
  color: var(--brand-error, #d03050);
  margin: 4px 0;
  padding: 4px 8px;
  background: color-mix(in srgb, var(--brand-error, #d03050) 6%, transparent);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}
.exec-event__actions {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-top: 6px;
  border-top: 1px dashed var(--border-subtle);
  font-size: 12px;
}
</style>
