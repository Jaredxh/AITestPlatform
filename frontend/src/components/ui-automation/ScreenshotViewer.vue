<template>
  <!-- 截图轮播（Task 10.5）。
       业务语义：把执行里"有截图的步骤"按时序串起来，让用户一帧一帧看
       浏览器实际执行画面（比看 reasoning 文字还直观）。

       数据源：``case.steps[].screenshot_path`` 非空时，前端走
       ``/api/ui-executions/steps/{step_id}/screenshot`` 拉图。

       UI 形态：
       - 顶部大图区（n-image 支持原图查看）
       - 左右翻页 + 键盘 ← / → 支持
       - 底部缩略图条，按用例分组、横向滚动；点击直接定位
       - 失败步骤的缩略图带红框提醒，"快速定位失败截图"按钮一键跳到第一张

       为什么不直接用 ``<n-image-group>``？
       - n-image-group 的 lightbox 没法把"步骤序号 / 用例归属 / 状态"信息
         同时显示；自己做一层壳更适合调试场景。 -->
  <div v-if="frames.length === 0" class="screenshot-viewer__empty">
    <span class="i-carbon-image" />
    <span>本次执行没有截图记录</span>
  </div>

  <div v-else class="screenshot-viewer">
    <div class="screenshot-viewer__head">
      <div class="screenshot-viewer__head-info">
        <strong>截图 {{ activeIndex + 1 }} / {{ frames.length }}</strong>
        <span class="screenshot-viewer__head-meta">
          <span :class="statusIcon(active.status)" />
          {{ statusLabel(active.status) }}
          · {{ active.caseTitle }}
          · 步骤 {{ active.stepNumber }}
        </span>
      </div>
      <div class="screenshot-viewer__head-actions">
        <n-button
          v-if="firstFailedIndex >= 0"
          quaternary
          size="tiny"
          @click="jumpTo(firstFailedIndex)"
        >
          <template #icon><span class="i-carbon-warning-alt-filled" /></template>
          跳到首张失败截图
        </n-button>
        <n-button
          quaternary
          size="tiny"
          tag="a"
          :href="active.url"
          target="_blank"
        >
          <template #icon><span class="i-carbon-launch" /></template>
          原图新窗口打开
        </n-button>
      </div>
    </div>

    <div class="screenshot-viewer__stage">
      <button
        type="button"
        class="screenshot-viewer__nav screenshot-viewer__nav--prev"
        :disabled="activeIndex === 0"
        title="上一张（←）"
        @click="prev"
      >
        <span class="i-carbon-chevron-left" />
      </button>
      <div class="screenshot-viewer__image-wrap">
        <n-image
          :key="active.stepId"
          :src="active.url"
          object-fit="contain"
          class="screenshot-viewer__image"
          @error="markBroken(active.stepId)"
        >
          <template #placeholder>
            <div class="screenshot-viewer__placeholder">
              <span class="i-carbon-image" />
              加载中…
            </div>
          </template>
        </n-image>
        <div
          v-if="brokenSet.has(active.stepId)"
          class="screenshot-viewer__broken"
        >
          <span class="i-carbon-image-search" />
          截图加载失败（文件可能已被清理）
        </div>
      </div>
      <button
        type="button"
        class="screenshot-viewer__nav screenshot-viewer__nav--next"
        :disabled="activeIndex >= frames.length - 1"
        title="下一张（→）"
        @click="next"
      >
        <span class="i-carbon-chevron-right" />
      </button>
    </div>

    <!-- 缩略图条（按 case 分组） -->
    <div class="screenshot-viewer__thumbs">
      <div
        v-for="group in groupedFrames"
        :key="group.caseId"
        class="screenshot-viewer__thumb-group"
      >
        <div class="screenshot-viewer__thumb-group-head">
          <span :class="statusIcon(group.caseStatus)" />
          <span class="screenshot-viewer__thumb-group-title">
            #{{ group.sortOrder + 1 }} {{ group.caseTitle }}
          </span>
        </div>
        <div class="screenshot-viewer__thumb-row">
          <button
            v-for="f in group.frames"
            :key="f.stepId"
            type="button"
            class="screenshot-viewer__thumb"
            :class="[
              `screenshot-viewer__thumb--${f.status}`,
              f.index === activeIndex ? 'screenshot-viewer__thumb--active' : '',
            ]"
            :title="`步骤 ${f.stepNumber} · ${statusLabel(f.status)}`"
            @click="jumpTo(f.index)"
          >
            <img
              v-if="!brokenSet.has(f.stepId)"
              :src="f.url"
              loading="lazy"
              alt=""
              @error="markBroken(f.stepId)"
            />
            <div v-else class="screenshot-viewer__thumb-broken">
              <span class="i-carbon-image-search" />
            </div>
            <span class="screenshot-viewer__thumb-tag">{{ f.stepNumber }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { NButton, NImage } from "naive-ui";

export interface ScreenshotStepInput {
  step_id: string;
  step_number: number;
  status: string;
  url: string;
}

export interface ScreenshotCaseInput {
  case_id: string;
  case_title: string;
  case_status: string;
  sort_order: number;
  steps: ScreenshotStepInput[];
}

const props = defineProps<{
  /** 已经按 case 分组的截图数据（父级展开 cases 时只 push 有截图的 step） */
  groups: ScreenshotCaseInput[];
}>();

// ─── flatten + groups ───────────────────────────────────────────

interface Frame {
  index: number;
  stepId: string;
  stepNumber: number;
  status: string;
  url: string;
  caseId: string;
  caseTitle: string;
}

const frames = computed<Frame[]>(() => {
  const out: Frame[] = [];
  let idx = 0;
  const sortedGroups = [...props.groups].sort((a, b) => a.sort_order - b.sort_order);
  for (const g of sortedGroups) {
    const sortedSteps = [...g.steps].sort((a, b) => a.step_number - b.step_number);
    for (const s of sortedSteps) {
      out.push({
        index: idx++,
        stepId: s.step_id,
        stepNumber: s.step_number,
        status: s.status,
        url: s.url,
        caseId: g.case_id,
        caseTitle: g.case_title,
      });
    }
  }
  return out;
});

interface ThumbGroup {
  caseId: string;
  caseTitle: string;
  caseStatus: string;
  sortOrder: number;
  frames: Frame[];
}

const groupedFrames = computed<ThumbGroup[]>(() => {
  const sortedGroups = [...props.groups].sort((a, b) => a.sort_order - b.sort_order);
  const indexByStepId = new Map(frames.value.map((f) => [f.stepId, f]));
  return sortedGroups.map((g) => {
    const fr = g.steps
      .map((s) => indexByStepId.get(s.step_id))
      .filter((x): x is Frame => !!x)
      .sort((a, b) => a.stepNumber - b.stepNumber);
    return {
      caseId: g.case_id,
      caseTitle: g.case_title,
      caseStatus: g.case_status,
      sortOrder: g.sort_order,
      frames: fr,
    };
  });
});

const firstFailedIndex = computed(() =>
  frames.value.findIndex((f) =>
    f.status === "failed" || f.status === "error" || f.status === "blocked_by_security",
  ),
);

// ─── 当前帧 ───────────────────────────────────────────────────────

const activeIndex = ref(0);

const active = computed<Frame>(
  () => frames.value[activeIndex.value] ?? {
    index: 0,
    stepId: "",
    stepNumber: 0,
    status: "",
    url: "",
    caseId: "",
    caseTitle: "",
  },
);

watch(
  () => frames.value.length,
  (n) => {
    if (activeIndex.value >= n) activeIndex.value = Math.max(0, n - 1);
  },
);

function jumpTo(idx: number) {
  if (idx < 0 || idx >= frames.value.length) return;
  activeIndex.value = idx;
}

function prev() {
  if (activeIndex.value > 0) activeIndex.value--;
}

function next() {
  if (activeIndex.value < frames.value.length - 1) activeIndex.value++;
}

// ─── broken 截图兜底 ─────────────────────────────────────────────

const brokenSet = ref(new Set<string>());

function markBroken(stepId: string) {
  if (brokenSet.value.has(stepId)) return;
  const next = new Set(brokenSet.value);
  next.add(stepId);
  brokenSet.value = next;
}

// ─── 键盘导航 ────────────────────────────────────────────────────

function onKey(e: KeyboardEvent) {
  // 避免在文本输入框里劫持光标键
  const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
  if (tag === "input" || tag === "textarea") return;
  if (e.key === "ArrowLeft") prev();
  if (e.key === "ArrowRight") next();
}

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));

// ─── 状态映射 ────────────────────────────────────────────────────

function statusLabel(s: string): string {
  if (s === "passed") return "通过";
  if (s === "failed") return "失败";
  if (s === "error") return "错误";
  if (s === "skipped") return "跳过";
  if (s === "blocked_by_security") return "被安全拦截";
  if (s === "running") return "执行中";
  return s || "—";
}

function statusIcon(s: string): string {
  if (s === "passed") return "i-carbon-checkmark-filled";
  if (s === "failed" || s === "error") return "i-carbon-error";
  if (s === "skipped") return "i-carbon-arrow-right";
  if (s === "blocked_by_security") return "i-carbon-security";
  return "i-carbon-time";
}

defineExpose({ jumpTo, jumpToFirstFailed: () => jumpTo(firstFailedIndex.value) });
</script>

<style scoped>
.screenshot-viewer__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-tertiary);
  padding: 24px 0;
  font-style: italic;
}

.screenshot-viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.screenshot-viewer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.screenshot-viewer__head-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}

.screenshot-viewer__head-meta {
  font-size: 12px;
  color: var(--text-tertiary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.screenshot-viewer__head-actions {
  display: inline-flex;
  gap: 6px;
}

.screenshot-viewer__stage {
  display: flex;
  align-items: stretch;
  gap: 6px;
  background: var(--bg-page-soft);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 8px;
  min-height: 320px;
}

.screenshot-viewer__nav {
  width: 32px;
  background: transparent;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  font: inherit;
  transition:
    background-color var(--duration-fast) var(--easing-standard),
    color var(--duration-fast) var(--easing-standard);
  flex-shrink: 0;
}

.screenshot-viewer__nav:hover:not(:disabled) {
  background: var(--bg-active);
  color: var(--brand-primary);
}

.screenshot-viewer__nav:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.screenshot-viewer__image-wrap {
  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1f2937;
  border-radius: var(--radius-sm);
  overflow: hidden;
  min-height: 280px;
}

.screenshot-viewer__image {
  max-height: 480px;
  max-width: 100%;
}

.screenshot-viewer__image :deep(img) {
  max-height: 480px;
  width: auto;
  display: block;
}

.screenshot-viewer__placeholder {
  display: flex;
  align-items: center;
  gap: 6px;
  color: rgba(255, 255, 255, 0.6);
  font-size: 12px;
  padding: 40px 0;
}

.screenshot-viewer__broken {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: rgba(255, 255, 255, 0.8);
  font-size: 13px;
  pointer-events: none;
}

.screenshot-viewer__thumbs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 240px;
  overflow-y: auto;
  padding-right: 4px;
}

.screenshot-viewer__thumb-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.screenshot-viewer__thumb-group-head {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.screenshot-viewer__thumb-group-title {
  font-weight: 600;
  color: var(--text-secondary);
}

.screenshot-viewer__thumb-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.screenshot-viewer__thumb {
  position: relative;
  width: 96px;
  height: 60px;
  border: 2px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
  cursor: pointer;
  background: #111827;
  padding: 0;
  flex-shrink: 0;
  transition:
    border-color var(--duration-fast) var(--easing-standard),
    transform var(--duration-fast) var(--easing-standard);
}

.screenshot-viewer__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.screenshot-viewer__thumb-broken {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.4);
  font-size: 18px;
}

.screenshot-viewer__thumb-tag {
  position: absolute;
  top: 2px;
  left: 2px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 999px;
  line-height: 1.2;
}

.screenshot-viewer__thumb:hover {
  border-color: var(--brand-primary-border);
  transform: translateY(-1px);
}

.screenshot-viewer__thumb--active {
  border-color: var(--brand-primary);
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.3);
}

.screenshot-viewer__thumb--failed,
.screenshot-viewer__thumb--error,
.screenshot-viewer__thumb--blocked_by_security {
  border-color: rgba(239, 68, 68, 0.5);
}

.screenshot-viewer__thumb--passed {
  border-color: rgba(22, 163, 74, 0.5);
}
</style>
