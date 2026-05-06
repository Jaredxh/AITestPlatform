<template>
  <!-- Accessibility tree YAML 查看器（Task 10.5）。
       业务语义：把 ``step.snapshot_after``（或 snapshot_before）按行结构化高亮
       展示。snapshot 是 ``@playwright/mcp`` 的输出——YAML 风格的层次化文本，
       每行形如 ``- button "Login" [ref=e123]``。

       高亮策略（不解析 YAML 语法，纯正则按行染色，与后端 snapshot_clipper
       的"行结构化"对齐）：
       - role 关键字（button/link/textbox/heading/img/...）→ 蓝紫色加粗
       - "name" 双引号字符串                              → 暖橙色
       - [ref=e123]                                       → 紫色 chip + 点击复制
       - main / dialog / region                           → 粗体 + 浅色背景

       为什么不直接用通用 highlighter（hljs / shiki）？
       - 体积太大；这里 snapshot 只有 ~3000 字符，自己写正则更轻
       - 通用 highlighter 不识别 ``[ref=...]`` 这种自定义语义，渲染也丑

       UI：
       - 顶部 segmented 选 before / after（前一种少见；后一种几乎总有）
       - 字符数统计 + "复制原文"按钮
       - 行号 + 等宽字体 + 横向滚动
       - 大文本（>500 行）默认折叠，按钮"全部展开" -->
  <div class="snapshot-viewer">
    <div v-if="!snapshotBefore && !snapshotAfter" class="snapshot-viewer__empty">
      <span class="i-carbon-tree-view-alt" />
      <span>本步骤没有保留页面结构快照</span>
    </div>
    <template v-else>
      <div class="snapshot-viewer__head">
        <n-radio-group
          v-if="hasBoth"
          v-model:value="mode"
          size="small"
        >
          <n-radio-button value="after">
            执行后页面结构
          </n-radio-button>
          <n-radio-button value="before">
            执行前页面结构
          </n-radio-button>
        </n-radio-group>
        <span v-else class="snapshot-viewer__head-tag">
          {{ snapshotAfter ? "执行后页面结构" : "执行前页面结构" }}
        </span>
        <span class="snapshot-viewer__head-meta">
          {{ lines.length }} 行 · {{ activeText.length.toLocaleString() }} 字符
          <span v-if="refCount > 0">
            · {{ refCount }} 个元素引用
          </span>
        </span>
        <div class="snapshot-viewer__head-actions">
          <n-button
            v-if="lines.length > previewLineLimit && !showAll"
            quaternary
            size="tiny"
            @click="showAll = true"
          >
            <template #icon><span class="i-carbon-expand-categories" /></template>
            展开全部 {{ lines.length }} 行
          </n-button>
          <n-button quaternary size="tiny" @click="copyText">
            <template #icon>
              <span :class="copied ? 'i-carbon-checkmark' : 'i-carbon-copy'" />
            </template>
            {{ copied ? "已复制" : "复制原文" }}
          </n-button>
        </div>
      </div>

      <pre class="snapshot-viewer__body" @click="onLineClick">
<span
          v-for="line in renderedLines"
          :key="line.lineNumber"
          class="snapshot-viewer__line"
        ><span class="snapshot-viewer__lineno">{{ line.lineNumber }}</span><span class="snapshot-viewer__content" v-html="line.html" /></span>
        <span
          v-if="!showAll && lines.length > previewLineLimit"
          class="snapshot-viewer__more"
        >
          … 还有 {{ lines.length - previewLineLimit }} 行未展示
        </span>
      </pre>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { NButton, NRadioButton, NRadioGroup } from "naive-ui";

const props = defineProps<{
  snapshotBefore?: string | null;
  snapshotAfter?: string | null;
}>();

const emit = defineEmits<{
  /** 用户点击 [ref=e123] → 父组件可用作"高亮元素"等扩展 */
  "ref-click": [ref: string];
}>();

// ─── 模式切换 ──────────────────────────────────────────────────────

const mode = ref<"before" | "after">(
  props.snapshotAfter ? "after" : "before",
);

const hasBoth = computed(() => !!props.snapshotBefore && !!props.snapshotAfter);

const activeText = computed(() => {
  if (mode.value === "after" && props.snapshotAfter) return props.snapshotAfter;
  if (mode.value === "before" && props.snapshotBefore) return props.snapshotBefore;
  return props.snapshotAfter || props.snapshotBefore || "";
});

const lines = computed(() => activeText.value.split("\n"));

// ─── 高亮 ─────────────────────────────────────────────────────────

/** 已知 ARIA roles —— Playwright MCP snapshot 里最常出现的那些 */
const ROLE_KEYWORDS = new Set([
  "button", "link", "textbox", "heading", "img", "image", "list", "listitem",
  "checkbox", "radio", "combobox", "option", "menu", "menuitem", "menubar",
  "tab", "tablist", "tabpanel", "dialog", "alertdialog", "alert", "status",
  "main", "navigation", "banner", "contentinfo", "complementary", "region",
  "form", "search", "article", "section", "table", "row", "cell", "grid",
  "gridcell", "row", "rowgroup", "rowheader", "columnheader", "tree",
  "treeitem", "switch", "slider", "spinbutton", "progressbar", "tooltip",
  "separator", "presentation", "none", "group", "toolbar", "definition",
  "term", "paragraph", "code", "blockquote", "iframe", "log",
]);

const REGION_KEYWORDS = new Set(["main", "dialog", "alertdialog", "navigation", "banner"]);

const REF_RE = /\[ref=([A-Za-z0-9_\-:.]+)\]/g;
const NAME_RE = /"([^"\\]*(?:\\.[^"\\]*)*)"/g;
const ROLE_RE = /^(\s*-?\s*)([a-z][a-z0-9_-]*)\b/i;

let refCounter = 0;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

interface RenderedLine {
  lineNumber: number;
  html: string;
}

const renderedLines = computed<RenderedLine[]>(() => {
  refCounter = 0;
  const sliced = showAll.value ? lines.value : lines.value.slice(0, previewLineLimit);
  return sliced.map((raw, idx) => ({
    lineNumber: idx + 1,
    html: highlight(raw),
  }));
});

const refCount = computed(() => {
  let n = 0;
  for (const line of lines.value) {
    n += (line.match(REF_RE) ?? []).length;
  }
  return n;
});

function highlight(line: string): string {
  if (!line) return "&nbsp;";
  let out = escapeHtml(line);

  // 1. role 关键字（行首第一个 token）
  out = out.replace(ROLE_RE, (_m, prefix: string, role: string) => {
    const lc = role.toLowerCase();
    if (REGION_KEYWORDS.has(lc)) {
      return `${prefix}<span class="snapshot-viewer__region">${role}</span>`;
    }
    if (ROLE_KEYWORDS.has(lc)) {
      return `${prefix}<span class="snapshot-viewer__role">${role}</span>`;
    }
    return `${prefix}${role}`;
  });

  // 2. "name" 字符串 → 暖橙色
  out = out.replace(NAME_RE, (_m, captured: string) =>
    `<span class="snapshot-viewer__name">"${captured}"</span>`,
  );

  // 3. [ref=e123] → 紫色 chip（带 data-ref 让点击委托）
  out = out.replace(REF_RE, (_m, refId: string) => {
    refCounter++;
    return `<button type="button" class="snapshot-viewer__ref" data-ref="${escapeHtml(refId)}" title="点击复制 ref" tabindex="-1">[ref=${escapeHtml(refId)}]</button>`;
  });

  return out;
}

// ─── 折叠 ─────────────────────────────────────────────────────────

const previewLineLimit = 500;
const showAll = ref(false);

watch(mode, () => {
  showAll.value = false;
});

// ─── 复制 ─────────────────────────────────────────────────────────

const copied = ref(false);

async function copyText() {
  try {
    await navigator.clipboard.writeText(activeText.value);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    // navigator.clipboard 在非 https / 老浏览器下不可用，静默失败
  }
}

// ─── ref 点击委托 ─────────────────────────────────────────────────

async function onLineClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null;
  if (!target?.classList.contains("snapshot-viewer__ref")) return;
  const refId = target.dataset.ref;
  if (!refId) return;
  emit("ref-click", refId);
  try {
    await navigator.clipboard.writeText(refId);
  } catch {
    /* ignore */
  }
  target.classList.add("snapshot-viewer__ref--flash");
  setTimeout(() => target.classList.remove("snapshot-viewer__ref--flash"), 600);
}
</script>

<style scoped>
.snapshot-viewer {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.snapshot-viewer__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
  font-style: italic;
  padding: 16px 0;
}

.snapshot-viewer__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.snapshot-viewer__head-tag {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-page-soft);
  padding: 2px 8px;
  border-radius: 999px;
}

.snapshot-viewer__head-meta {
  font-size: 11px;
  color: var(--text-tertiary);
}

.snapshot-viewer__head-actions {
  margin-left: auto;
  display: inline-flex;
  gap: 4px;
}

.snapshot-viewer__body {
  margin: 0;
  padding: 8px 0 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  max-height: 480px;
  overflow: auto;
  color: var(--text-primary);
  white-space: pre;
}

.snapshot-viewer__line {
  display: flex;
  align-items: flex-start;
  padding: 0 8px;
}

.snapshot-viewer__line:hover {
  background: var(--bg-active);
}

.snapshot-viewer__lineno {
  flex-shrink: 0;
  display: inline-block;
  min-width: 40px;
  padding-right: 12px;
  text-align: right;
  color: var(--text-tertiary);
  font-size: 11px;
  user-select: none;
}

.snapshot-viewer__content {
  flex: 1;
  min-width: 0;
}

.snapshot-viewer__more {
  display: block;
  padding: 6px 12px;
  color: var(--text-tertiary);
  font-style: italic;
  font-size: 11px;
}

/* 高亮调色板 */

.snapshot-viewer__role {
  color: #6366f1;
  font-weight: 600;
}

.snapshot-viewer__region {
  color: #6366f1;
  font-weight: 700;
  background: rgba(99, 102, 241, 0.08);
  padding: 0 2px;
  border-radius: 2px;
}

.snapshot-viewer__name {
  color: #b45309;
}

.snapshot-viewer__ref {
  display: inline-block;
  background: rgba(168, 85, 247, 0.14);
  color: #7c3aed;
  border: 1px solid rgba(168, 85, 247, 0.25);
  border-radius: 4px;
  padding: 0 4px;
  font-size: 11px;
  margin: 0 2px;
  cursor: pointer;
  font: inherit;
  font-weight: 600;
  vertical-align: middle;
  line-height: 1.4;
  transition: background-color var(--duration-fast) var(--easing-standard);
}

.snapshot-viewer__ref:hover {
  background: rgba(168, 85, 247, 0.25);
}

.snapshot-viewer__ref--flash {
  background: rgba(168, 85, 247, 0.5);
  color: #fff;
}
</style>
