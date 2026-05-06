<template>
  <!-- Tool-call 时间线（Task 10.4）。
       业务语义：把单步骤里 LLM 发起的所有 tool_call 按时序展示，方便排查
       "AI 这一步到底做了什么 / 为什么挂在哪里"。

       三种特殊高亮（与 SynthesizedDataCard / DataFailureCard 视觉对齐）：
         - 🟡 platform_synthesize_data         自造数据（warning 黄）
         - 🟠 platform_mark_data_failure       标记数据失败（error 橙）
         - 🔒 platform_get_secret              secret 取值（紫）；result.value
                                                  会被后端落库时改成 ``<secret used>``
                                                  这里再用占位 chip 做兜底，万一
                                                  历史数据漏脱敏前端也不显示明文

       其他 ``platform_*`` 默认 info 色，``browser_*`` / MCP 色 default。
       默认折叠 args + result，点开才展开 JSON——避免上百条 tool_call 时
       页面一屏滚不到底。 -->
  <div v-if="calls.length === 0" class="tool-timeline__empty">
    本步骤未触发任何 tool_call
  </div>
  <ol v-else class="tool-timeline">
    <li
      v-for="(call, idx) in calls"
      :key="`${call.raw_name || call.name}-${idx}`"
      class="tool-timeline__item"
      :class="`tool-timeline__item--${call._kind}`"
    >
      <div class="tool-timeline__marker">
        <span class="tool-timeline__dot">{{ call._dotEmoji }}</span>
        <span v-if="idx < calls.length - 1" class="tool-timeline__line" />
      </div>
      <div class="tool-timeline__body">
        <div class="tool-timeline__head">
          <span class="tool-timeline__action">{{ call._actionLabel }}</span>
          <code
            class="tool-timeline__name"
            :title="`原始工具名：${call._displayName}`"
          >{{ call._displayName }}</code>
          <span
            class="tool-timeline__chip"
            :class="`tool-timeline__chip--${call._kind}`"
          >
            {{ call._kindLabel }}
          </span>
          <span v-if="call.blocked" class="tool-timeline__chip tool-timeline__chip--blocked">
            <span class="i-carbon-security" /> 安全拦截
          </span>
          <span v-if="call.error" class="tool-timeline__chip tool-timeline__chip--error">
            <span class="i-carbon-warning-alt" /> 错误
          </span>
          <span v-if="typeof call.duration_ms === 'number'" class="tool-timeline__meta">
            <span class="i-carbon-time" />{{ formatDuration(call.duration_ms) }}
          </span>
          <button
            type="button"
            class="tool-timeline__toggle"
            @click="toggle(idx)"
          >
            <span :class="isExpanded(idx) ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
            {{ isExpanded(idx) ? "收起" : "查看入参 / 结果" }}
          </button>
        </div>

        <!-- 高亮特殊语义的"摘要行"——展开前也能一眼看到关键信息 -->
        <div v-if="call._summary" class="tool-timeline__summary">
          {{ call._summary }}
        </div>

        <transition name="fade">
          <div v-if="isExpanded(idx)" class="tool-timeline__detail">
            <div class="tool-timeline__detail-block">
              <div class="tool-timeline__detail-label">入参</div>
              <pre class="tool-timeline__json">{{ formatJson(call.arguments) }}</pre>
            </div>
            <div class="tool-timeline__detail-block">
              <div class="tool-timeline__detail-label">
                返回结果
                <span v-if="call._secretRedacted" class="tool-timeline__secret-pill">
                  <span class="i-carbon-locked" />
                  已脱敏
                </span>
              </div>
              <pre class="tool-timeline__json">{{ formatJson(call._displayResult) }}</pre>
            </div>
            <div v-if="call.error" class="tool-timeline__detail-block">
              <div class="tool-timeline__detail-label tool-timeline__detail-label--error">
                错误信息
              </div>
              <pre class="tool-timeline__json tool-timeline__json--error">{{ call.error }}</pre>
            </div>
          </div>
        </transition>
      </div>
    </li>
  </ol>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

/**
 * 与后端 ``_serialize_tool_call`` 输出形状一致；result 在 persistence 入库
 * 时已 sanitize（secret value → ``"<secret used>"``）。
 */
export interface RawToolCall {
  name?: string;
  raw_name?: string;
  arguments?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  duration_ms?: number;
  blocked?: boolean;
  error?: string | null;
  snapshot_chars?: number;
}

type CallKind = "synthesize" | "mark_failure" | "secret" | "platform" | "browser" | "default";

interface DecoratedCall extends RawToolCall {
  _kind: CallKind;
  _kindLabel: string;
  _displayName: string;
  /** 中文化的动作描述（如"打开页面 / 点击元素"），便于非技术用户阅读；
   *  如果工具名不在已知映射里，回退到原始工具名（``_displayName``）。 */
  _actionLabel: string;
  _dotEmoji: string;
  _summary: string | null;
  _displayResult: Record<string, unknown> | null;
  _secretRedacted: boolean;
}

const SECRET_PLACEHOLDER = "<secret used>";

const props = defineProps<{
  toolCalls: RawToolCall[];
}>();

// ─── decorate ──────────────────────────────────────────────────────

function stripPrefix(name: string | undefined): string {
  if (!name) return "(unnamed)";
  return name.includes(":") ? name.slice(name.indexOf(":") + 1) : name;
}

function classify(rawName: string): CallKind {
  if (rawName.endsWith("platform_synthesize_data")) return "synthesize";
  if (rawName.endsWith("platform_mark_data_failure")) return "mark_failure";
  if (rawName.endsWith("platform_get_secret")) return "secret";
  if (rawName.startsWith("platform_")) return "platform";
  if (rawName.startsWith("browser_")) return "browser";
  return "default";
}

const KIND_META: Record<CallKind, { label: string; emoji: string }> = {
  synthesize: { label: "AI 自造", emoji: "🟡" },
  mark_failure: { label: "数据失败", emoji: "🟠" },
  secret: { label: "密钥", emoji: "🔒" },
  platform: { label: "平台工具", emoji: "🧩" },
  browser: { label: "浏览器", emoji: "🌐" },
  default: { label: "工具", emoji: "·" },
};

/** 工具名 → 中文人话动作。键来自 ``@playwright/mcp`` 自带工具集 + 我们
 *  自己注入的 ``platform_*`` 工具。映射缺失时退化为显示原始工具名。
 *
 *  保持英文工具名（``_displayName``）作为副标题展示给开发者，方便去 MCP
 *  文档对照——非技术用户读中文动作即可。 */
const ACTION_LABELS: Record<string, string> = {
  // ─ 浏览器导航 / 页面控制 ─────────────────────────────────────────
  browser_navigate: "打开页面",
  browser_navigate_back: "后退",
  browser_navigate_forward: "前进",
  browser_close: "关闭页面",
  browser_resize: "调整窗口尺寸",
  browser_install: "安装浏览器",
  browser_handle_dialog: "处理弹窗",
  // ─ 信息抓取 / 截图 ──────────────────────────────────────────────
  browser_snapshot: "获取页面结构",
  browser_take_screenshot: "截图",
  browser_screenshot: "截图",
  browser_pdf_save: "保存 PDF",
  browser_console_messages: "读取控制台日志",
  browser_network_requests: "读取网络请求",
  // ─ 元素交互 ─────────────────────────────────────────────────────
  browser_click: "点击元素",
  browser_hover: "悬停元素",
  browser_drag: "拖动元素",
  browser_type: "输入文本",
  browser_press_key: "按键",
  browser_select_option: "选择下拉项",
  browser_file_upload: "上传文件",
  browser_evaluate: "执行 JS",
  // ─ 等待 ─────────────────────────────────────────────────────────
  browser_wait_for: "等待条件",
  browser_wait_for_load_state: "等待页面加载",
  // ─ 标签 / 帧管理 ────────────────────────────────────────────────
  browser_tab_list: "查看标签页列表",
  browser_tab_new: "新建标签页",
  browser_tab_select: "切换标签页",
  browser_tab_close: "关闭标签页",
  // ─ 平台扩展工具（我们自己注入的）────────────────────────────────
  platform_get_secret: "读取密钥",
  platform_synthesize_data: "AI 自造数据",
  platform_mark_data_failure: "标记数据缺失",
  platform_assert: "执行断言",
  platform_get_test_data: "读取物料",
  platform_finish_step: "结束当前步骤",
};

function actionLabelFor(rawName: string, displayName: string): string {
  const direct = ACTION_LABELS[rawName] || ACTION_LABELS[displayName];
  if (direct) return direct;
  return displayName;
}

function summaryFor(kind: CallKind, args: Record<string, unknown>, result: Record<string, unknown>): string | null {
  if (kind === "synthesize") {
    const k = String(args.key ?? "");
    const src = String(result.source ?? "");
    const val = typeof result.value === "string" ? result.value : JSON.stringify(result.value ?? "");
    const valTrim = val.length > 60 ? `${val.slice(0, 60)}…` : val;
    return `自造 ${k}${src ? `（${src}）` : ""}${valTrim ? ` → ${valTrim}` : ""}`;
  }
  if (kind === "mark_failure") {
    const k = String(args.key ?? "");
    const reason = String(args.reason ?? "");
    return `标记失败 ${k}${reason ? `：${reason}` : ""}`;
  }
  if (kind === "secret") {
    const k = String(args.key ?? "");
    return `读取 secret ${k}（值已遮蔽）`;
  }
  return null;
}

const calls = computed<DecoratedCall[]>(() =>
  (props.toolCalls ?? []).map((raw) => {
    const rawName = raw.raw_name || raw.name || "";
    const kind = classify(rawName);
    const args = (raw.arguments ?? {}) as Record<string, unknown>;
    const result = (raw.result ?? {}) as Record<string, unknown>;
    const meta = KIND_META[kind];

    let displayResult: Record<string, unknown> | null = result;
    let secretRedacted = false;
    if (kind === "secret" || result._test_data_secret_used) {
      // 二次兜底：万一历史脏数据没脱敏，前端也强制把 value 字段抹掉
      const safe = { ...result };
      if ("value" in safe && safe.value !== SECRET_PLACEHOLDER) {
        safe.value = SECRET_PLACEHOLDER;
      }
      displayResult = safe;
      secretRedacted = true;
    }

    const displayName = stripPrefix(rawName);
    return {
      ...raw,
      _kind: kind,
      _kindLabel: meta.label,
      _dotEmoji: meta.emoji,
      _displayName: displayName,
      _actionLabel: actionLabelFor(rawName, displayName),
      _summary: summaryFor(kind, args, result),
      _displayResult: displayResult,
      _secretRedacted: secretRedacted,
    };
  }),
);

// ─── 折叠状态 ──────────────────────────────────────────────────────

const expanded = ref(new Set<number>());

function isExpanded(idx: number): boolean {
  return expanded.value.has(idx);
}

function toggle(idx: number) {
  const next = new Set(expanded.value);
  if (next.has(idx)) next.delete(idx);
  else next.add(idx);
  expanded.value = next;
}

// ─── helpers ──────────────────────────────────────────────────────

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatJson(v: unknown): string {
  if (v == null) return "(空)";
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
</script>

<style scoped>
.tool-timeline__empty {
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
  padding: 12px 0;
  font-style: italic;
}

.tool-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.tool-timeline__item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  position: relative;
  padding-bottom: 8px;
}

.tool-timeline__marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 22px;
}

.tool-timeline__dot {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--bg-card);
  border: 2px solid var(--border-default);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  z-index: 1;
}

.tool-timeline__item--synthesize .tool-timeline__dot {
  border-color: rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.08);
}

.tool-timeline__item--mark_failure .tool-timeline__dot {
  border-color: rgba(239, 68, 68, 0.4);
  background: rgba(239, 68, 68, 0.08);
}

.tool-timeline__item--secret .tool-timeline__dot {
  border-color: rgba(168, 85, 247, 0.4);
  background: rgba(168, 85, 247, 0.08);
}

.tool-timeline__item--platform .tool-timeline__dot {
  border-color: rgba(14, 165, 233, 0.4);
  background: rgba(14, 165, 233, 0.08);
}

.tool-timeline__item--browser .tool-timeline__dot {
  border-color: rgba(99, 102, 241, 0.4);
  background: rgba(99, 102, 241, 0.06);
}

.tool-timeline__line {
  flex: 1;
  width: 2px;
  background: var(--border-subtle);
  margin-top: 2px;
}

.tool-timeline__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 4px;
}

.tool-timeline__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-timeline__action {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-right: 2px;
}

.tool-timeline__name {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  font-weight: 500;
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  color: var(--text-tertiary);
}

.tool-timeline__chip {
  font-size: 10px;
  padding: 0 6px;
  min-height: 16px;
  line-height: 16px;
  border-radius: 999px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 1px solid transparent;
}

.tool-timeline__chip--synthesize {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.3);
}

.tool-timeline__chip--mark_failure {
  background: rgba(239, 68, 68, 0.16);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.3);
}

.tool-timeline__chip--secret {
  background: rgba(168, 85, 247, 0.14);
  color: #7c3aed;
  border-color: rgba(168, 85, 247, 0.3);
}

.tool-timeline__chip--platform {
  background: rgba(14, 165, 233, 0.12);
  color: #0369a1;
  border-color: rgba(14, 165, 233, 0.25);
}

.tool-timeline__chip--browser {
  background: rgba(99, 102, 241, 0.1);
  color: #4338ca;
  border-color: rgba(99, 102, 241, 0.25);
}

.tool-timeline__chip--default {
  background: var(--bg-page-soft);
  color: var(--text-secondary);
  border-color: var(--border-subtle);
}

.tool-timeline__chip--blocked {
  background: rgba(220, 38, 38, 0.16);
  color: #991b1b;
  border-color: rgba(220, 38, 38, 0.3);
}

.tool-timeline__chip--error {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.3);
}

.tool-timeline__meta {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.tool-timeline__toggle {
  margin-left: auto;
  font-size: 11px;
  background: transparent;
  border: 1px solid var(--border-default);
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  padding: 0 6px;
  cursor: pointer;
  font: inherit;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  line-height: 18px;
}

.tool-timeline__toggle:hover {
  background: var(--bg-active);
  color: var(--text-primary);
}

.tool-timeline__summary {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-page-soft);
  padding: 3px 8px;
  border-radius: 4px;
  font-style: italic;
  word-break: break-word;
}

.tool-timeline__item--synthesize .tool-timeline__summary {
  background: rgba(245, 158, 11, 0.06);
  color: #b45309;
  font-style: normal;
}

.tool-timeline__item--mark_failure .tool-timeline__summary {
  background: rgba(239, 68, 68, 0.06);
  color: #b91c1c;
  font-style: normal;
}

.tool-timeline__item--secret .tool-timeline__summary {
  background: rgba(168, 85, 247, 0.06);
  color: #7c3aed;
  font-style: normal;
}

.tool-timeline__detail {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
  padding: 8px 10px;
  background: var(--bg-page-soft);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
}

.tool-timeline__detail-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-timeline__detail-label--error {
  color: var(--color-error, #b91c1c);
}

.tool-timeline__secret-pill {
  text-transform: none;
  letter-spacing: normal;
  background: rgba(168, 85, 247, 0.14);
  color: #7c3aed;
  padding: 0 6px;
  border-radius: 999px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.tool-timeline__json {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  padding: 6px 8px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow: auto;
  color: var(--text-primary);
}

.tool-timeline__json--error {
  color: var(--color-error, #b91c1c);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--duration-base) var(--easing-standard);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
