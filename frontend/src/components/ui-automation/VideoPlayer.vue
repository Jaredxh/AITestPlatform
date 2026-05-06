<template>
  <!-- 视频播放器（Task 10.5）。
       业务语义：在执行详情主页里把 Playwright 录制的整段 webm 播出来，
       并在自定义时间轴上把每个 case 起止时间标成"章节"——点击章节
       直接跳到对应秒数。

       为什么不用 ``<video>`` 原生 controls？
       - 原生时间轴没法画"用例章节"标注；
       - 自己写 controls 就能让"通过/失败/data_failure" 用不同颜色染色，
         一眼看出哪个用例段挂掉。

       视频时间轴 ↔ 用例时间映射：
       ``case.started_at - execution.started_at`` 转秒（毫秒精度）。
       Playwright 录制是从 BrowserContext 创建即开始；与 execution.started_at
       的偏差通常 < 1s，对"跳转到大致位置"完全够用。

       兜底：
       - 视频还在加载（duration NaN）→ 章节宽度按 case.duration_ms 等比；
       - 某 case 没 started_at（比如 skipped）→ 不渲染章节；
       - error 加载失败 → 整块隐藏，显示 fallback 文案。 -->
  <div class="video-player">
    <div v-if="loadError" class="video-player__error">
      <span class="i-carbon-warning-alt-filled" />
      视频加载失败：{{ loadError }}
      <a class="video-player__error-link" :href="src" target="_blank">直接下载</a>
    </div>
    <template v-else>
      <div class="video-player__stage">
        <video
          ref="videoEl"
          class="video-player__video"
          :src="src"
          preload="metadata"
          controls
          @loadedmetadata="onLoaded"
          @timeupdate="onTimeUpdate"
          @error="onError"
        />
      </div>

      <div class="video-player__chapters" v-if="chapters.length > 0">
        <div class="video-player__chapters-head">
          <span class="i-carbon-bookmark" />
          按用例跳转
          <span class="video-player__chapters-hint">
            视频时长 {{ formatTime(duration) }}
            ；点击下方区段直接跳到对应用例
          </span>
        </div>
        <div
          class="video-player__bar"
          :style="{ '--bar-total': totalSeconds }"
        >
          <button
            v-for="ch in chapters"
            :key="ch.id"
            type="button"
            class="video-player__chapter"
            :class="[
              `video-player__chapter--${ch.status}`,
              ch.id === activeChapterId ? 'video-player__chapter--active' : '',
            ]"
            :style="chapterStyle(ch)"
            :title="`${ch.title} · ${formatTime(ch.startSec)} → ${formatTime(ch.endSec)} · ${ch.statusLabel}`"
            @click="jumpTo(ch.startSec)"
          >
            <span class="video-player__chapter-label">
              #{{ ch.sortOrder + 1 }} {{ ch.title }}
            </span>
          </button>
          <!-- 当前播放头位置 -->
          <div
            class="video-player__cursor"
            :style="{ left: `${cursorPercent}%` }"
          />
        </div>
        <div class="video-player__chapters-legend">
          <span class="video-player__legend-item video-player__legend-item--passed">
            <span class="video-player__legend-dot" /> 通过
          </span>
          <span class="video-player__legend-item video-player__legend-item--failed">
            <span class="video-player__legend-dot" /> 失败 / 错误
          </span>
          <span class="video-player__legend-item video-player__legend-item--data_failure">
            <span class="video-player__legend-dot" /> 数据失败
          </span>
          <span class="video-player__legend-item video-player__legend-item--other">
            <span class="video-player__legend-dot" /> 跳过 / 其它
          </span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

export interface CaseChapterInput {
  id: string;
  title: string;
  sort_order: number;
  status: string;
  data_confidence?: string | null;
  /** ISO8601 字符串；缺省（pending/skipped）表示无法定位 → 不渲染章节 */
  started_at?: string | null;
  completed_at?: string | null;
  /** 兜底：started_at 缺时用来估算章节宽度 */
  duration_ms?: number | null;
}

const props = defineProps<{
  /** 视频 URL（已经带项目前缀，比如 ``/api/ui-executions/{id}/video``） */
  src: string;
  /** 用例数据用于绘制章节 */
  cases: CaseChapterInput[];
  /** execution.started_at —— 与 case.started_at 求差得到秒偏移 */
  executionStartedAt?: string | null;
  /** execution.duration_ms —— 章节兜底估算时用 */
  executionDurationMs?: number | null;
}>();

// ─── refs / state ─────────────────────────────────────────────────

const videoEl = ref<HTMLVideoElement | null>(null);
const duration = ref(0);
const currentTime = ref(0);
const loadError = ref<string | null>(null);

// ─── chapters ─────────────────────────────────────────────────────

interface Chapter {
  id: string;
  title: string;
  sortOrder: number;
  status: "passed" | "failed" | "data_failure" | "other";
  statusLabel: string;
  startSec: number;
  endSec: number;
}

const chapters = computed<Chapter[]>(() => {
  const startMs = props.executionStartedAt
    ? new Date(props.executionStartedAt).getTime()
    : null;
  const list: Chapter[] = [];
  for (const c of props.cases) {
    if (!c.started_at && !c.duration_ms) continue;
    let startSec = 0;
    let endSec = 0;
    if (startMs && c.started_at) {
      startSec = Math.max(0, (new Date(c.started_at).getTime() - startMs) / 1000);
      const endMs = c.completed_at
        ? new Date(c.completed_at).getTime()
        : (c.duration_ms ? new Date(c.started_at).getTime() + c.duration_ms : null);
      endSec = endMs
        ? Math.max(startSec + 0.5, (endMs - startMs) / 1000)
        : startSec + 1;
    } else if (c.duration_ms) {
      // 没 started_at 时按 sort_order 估算（会出现在 execution_started_at 缺失的兜底场景）
      const prev = list[list.length - 1];
      startSec = prev ? prev.endSec : 0;
      endSec = startSec + c.duration_ms / 1000;
    }
    list.push({
      id: c.id,
      title: c.title || `用例 ${c.sort_order + 1}`,
      sortOrder: c.sort_order,
      status: classify(c.status, c.data_confidence),
      statusLabel: statusLabel(c.status, c.data_confidence),
      startSec,
      endSec,
    });
  }
  return list.sort((a, b) => a.sortOrder - b.sortOrder);
});

function classify(
  status: string,
  conf?: string | null,
): "passed" | "failed" | "data_failure" | "other" {
  if (conf === "data_failure") return "data_failure";
  if (status === "passed") return "passed";
  if (status === "failed" || status === "error") return "failed";
  return "other";
}

function statusLabel(status: string, conf?: string | null): string {
  if (conf === "data_failure") return "数据失败";
  if (status === "passed") return "通过";
  if (status === "failed") return "失败";
  if (status === "error") return "错误";
  if (status === "skipped") return "跳过";
  return status;
}

const totalSeconds = computed(() => {
  if (duration.value > 0) return duration.value;
  if (props.executionDurationMs) return props.executionDurationMs / 1000;
  // 章节估算下兜底
  const last = chapters.value[chapters.value.length - 1];
  return last ? Math.max(1, last.endSec) : 1;
});

function chapterStyle(ch: Chapter): Record<string, string> {
  const total = totalSeconds.value || 1;
  const left = (Math.min(ch.startSec, total) / total) * 100;
  const width = Math.max(2, ((Math.min(ch.endSec, total) - ch.startSec) / total) * 100);
  return {
    left: `${left}%`,
    width: `${width}%`,
  };
}

const cursorPercent = computed(() => {
  if (totalSeconds.value <= 0) return 0;
  return Math.min(100, (currentTime.value / totalSeconds.value) * 100);
});

const activeChapterId = computed(() => {
  const sec = currentTime.value;
  for (const ch of chapters.value) {
    if (sec >= ch.startSec && sec <= ch.endSec) return ch.id;
  }
  return null;
});

// ─── handlers ─────────────────────────────────────────────────────

function onLoaded() {
  if (!videoEl.value) return;
  const d = videoEl.value.duration;
  duration.value = Number.isFinite(d) ? d : 0;
  loadError.value = null;
}

function onTimeUpdate() {
  if (!videoEl.value) return;
  currentTime.value = videoEl.value.currentTime;
}

function onError() {
  loadError.value = "无法加载视频文件（可能尚未生成或权限不足）";
}

function jumpTo(seconds: number) {
  if (!videoEl.value) return;
  videoEl.value.currentTime = Math.max(0, seconds);
  videoEl.value.play().catch(() => {
    // play() 在自动播放策略下可能 reject；不处理，让用户手动按播放
  });
}

// ─── helpers ──────────────────────────────────────────────────────

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const min = Math.floor(seconds / 60);
  const sec = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${min}:${sec}`;
}

defineExpose({ jumpTo });
</script>

<style scoped>
.video-player {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.video-player__error {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-error, #b91c1c);
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
}

.video-player__error-link {
  color: var(--brand-primary);
  text-decoration: underline;
  margin-left: auto;
}

.video-player__stage {
  background: #000;
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  justify-content: center;
}

.video-player__video {
  width: 100%;
  max-height: 480px;
  display: block;
  background: #000;
}

.video-player__chapters {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.video-player__chapters-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 600;
}

.video-player__chapters-hint {
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.video-player__bar {
  position: relative;
  height: 26px;
  background: var(--bg-page-soft);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.video-player__chapter {
  position: absolute;
  top: 0;
  bottom: 0;
  border: none;
  cursor: pointer;
  font: inherit;
  padding: 0 4px;
  display: flex;
  align-items: center;
  overflow: hidden;
  transition:
    transform var(--duration-fast) var(--easing-standard),
    box-shadow var(--duration-fast) var(--easing-standard);
  border-right: 1px solid rgba(255, 255, 255, 0.5);
}

.video-player__chapter:last-child {
  border-right: none;
}

.video-player__chapter--passed {
  background: rgba(22, 163, 74, 0.45);
  color: #fff;
}

.video-player__chapter--failed {
  background: rgba(239, 68, 68, 0.55);
  color: #fff;
}

.video-player__chapter--data_failure {
  background: rgba(245, 158, 11, 0.6);
  color: #fff;
}

.video-player__chapter--other {
  background: rgba(148, 163, 184, 0.45);
  color: #fff;
}

.video-player__chapter:hover {
  filter: brightness(1.15);
}

.video-player__chapter--active {
  box-shadow: inset 0 0 0 2px var(--brand-primary), 0 0 0 1px var(--brand-primary);
  z-index: 1;
}

.video-player__chapter-label {
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-shadow: 0 1px 1px rgba(0, 0, 0, 0.4);
}

.video-player__cursor {
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 2px;
  background: var(--text-primary);
  pointer-events: none;
  z-index: 2;
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.4);
}

.video-player__chapters-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.video-player__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.video-player__legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}

.video-player__legend-item--passed .video-player__legend-dot {
  background: rgba(22, 163, 74, 0.6);
}
.video-player__legend-item--failed .video-player__legend-dot {
  background: rgba(239, 68, 68, 0.6);
}
.video-player__legend-item--data_failure .video-player__legend-dot {
  background: rgba(245, 158, 11, 0.7);
}
.video-player__legend-item--other .video-player__legend-dot {
  background: rgba(148, 163, 184, 0.6);
}
</style>
