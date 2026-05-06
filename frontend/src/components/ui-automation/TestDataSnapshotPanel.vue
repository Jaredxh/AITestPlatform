<template>
  <!-- 物料快照面板（Task 10.3）。
       数据来源：``ExecutionDetailResponse.test_data_snapshot``——
       即 ``TestDataResolver.serialize_for_audit()`` 的输出（写入数据库时已经
       脱敏 secret 明文）。

       2026-05 验收反馈调整后：snapshot 在落库时已经按"本次显式配置的物料
       集 id"过滤过（``configured_set_ids = loaded ∪ env_default ∪ tc_default``），
       项目/个人 scope 自动合并的物料不再污染面板，让用户一眼看到"我配置的
       就这些"。AI 自造、manual override、adhoc 始终保留。

       展示分组：
       1. 已加载物料明细（已按上面规则过滤）
       2. 临时覆盖（manual_overrides，从 config_snapshot 读）
       3. AI 自造的 keys（synthetic_source != null，独立成 chip 区）

       Secret 类型不做"点击展开" —— 后端在序列化时就已经把明文换掉成
       ``<secret:redacted>``。前端展示时把这个内部占位符替换成中文锁图标
       ``🔒 已脱敏`` —— 直接打 ``<secret:redacted>`` 给最终用户看会有「这
       是真实值还是占位符？」的歧义。 -->
  <n-card
    size="small"
    class="snapshot-panel"
    :bordered="false"
    embedded
  >
    <template #header>
      <div class="snapshot-panel__head">
        <span class="i-carbon-data-base" />
        <span>物料快照</span>
        <span class="snapshot-panel__count">
          共 {{ items.length }} 项
          <template v-if="syntheticItems.length > 0">
            · <span class="text-warning">{{ syntheticItems.length }} 项 AI 自造</span>
          </template>
          <template v-if="manualOverrideKeys.length > 0">
            · <span class="text-info">{{ manualOverrideKeys.length }} 项临时覆盖</span>
          </template>
        </span>
      </div>
    </template>
    <template #header-extra>
      <n-button text size="small" @click="expanded = !expanded">
        <template #icon>
          <span :class="expanded ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
        </template>
        {{ expanded ? "收起" : "展开" }}
      </n-button>
    </template>

    <transition name="fade">
      <div v-if="expanded" class="snapshot-panel__body">
        <app-empty
          v-if="items.length === 0"
          size="small"
          icon="i-carbon-data-base"
          title="未记录物料快照"
          description="该执行可能未触发任何物料合并；或调度时未 flush 快照"
        />

        <template v-else>
          <!-- AI 自造的 keys：独立成醒目的黄色区域 -->
          <div v-if="syntheticItems.length > 0" class="snapshot-panel__synth">
            <div class="snapshot-panel__synth-head">
              🟡 本次执行 AI 自造了 {{ syntheticItems.length }} 项数据
            </div>
            <div class="snapshot-panel__synth-list">
              <span
                v-for="item in syntheticItems"
                :key="`synth-${item.key}`"
                class="snapshot-panel__synth-chip"
              >
                <code>{{ item.key }}</code>
                <span v-if="item.synthetic_source" class="snapshot-panel__synth-source">
                  · {{ item.synthetic_source }}
                </span>
              </span>
            </div>
          </div>

          <!-- 临时覆盖（manual_overrides） -->
          <div v-if="manualOverrideKeys.length > 0" class="snapshot-panel__overrides">
            <div class="snapshot-panel__overrides-head">
              <span class="i-carbon-edit" />
              本次执行临时覆盖（manual_overrides）
            </div>
            <n-data-table
              :columns="overrideColumns"
              :data="overrideRows"
              size="small"
              :bordered="false"
              :pagination="false"
            />
          </div>

          <!-- 完整物料表格 -->
          <div class="snapshot-panel__table">
            <div class="snapshot-panel__table-head">
              <span class="i-carbon-list-checked" />
              已加载物料明细
              <span class="snapshot-panel__table-hint">
                仅展示本次配置的物料集明细；secret 已脱敏
              </span>
            </div>
            <n-data-table
              :columns="itemColumns"
              :data="items"
              :pagination="paginationProps"
              size="small"
              :bordered="false"
              :row-class-name="rowClass"
            />
          </div>
        </template>
      </div>
    </transition>
  </n-card>
</template>

<script setup lang="ts">
import { computed, h, ref } from "vue";
import { NButton, NCard, NDataTable, NTag } from "naive-ui";
import type { DataTableColumns, PaginationProps } from "naive-ui";
import AppEmpty from "@/components/common/AppEmpty.vue";

// ─── 类型 ─────────────────────────────────────────────────────────

/** 与后端 ``TestDataItem.to_audit_blob`` 一致的形状（字段为联合可选） */
export interface SnapshotItem {
  key: string;
  value_type: string;
  description?: string | null;
  /** 仅 string / multiline 类型（dataset/file/secret 不会有） */
  value_text?: string | null;
  /** dataset 类型 */
  value_json?: unknown;
  /** file 类型 */
  file_name?: string | null;
  file_size?: number | null;
  file_mime?: string | null;
  /** secret 类型时为 ``"<secret:redacted>"`` */
  value?: string | null;
  /** 自造来源（如 ``ai`` / ``heuristic_phone``）；非自造为 undefined */
  synthetic_source?: string | null;
  /** 源物料集 id；adhoc / manual override / 自造 时为 undefined */
  source_set_id?: string | null;
  /** 源物料集名称；用于在快照表里直观展示"该 key 来自哪个物料集" */
  source_set_name?: string | null;
}

const props = withDefaults(
  defineProps<{
    /** 后端 ``test_data_snapshot`` 字段：``{ key: SnapshotItem }`` */
    snapshot?: Record<string, SnapshotItem> | null;
    /** ``config_snapshot.manual_overrides``，用于"临时覆盖"区 */
    manualOverrides?: Record<string, unknown> | null;
    /** 默认是否展开 */
    defaultExpanded?: boolean;
  }>(),
  {
    snapshot: null,
    manualOverrides: null,
    defaultExpanded: false,
  },
);

const expanded = ref(props.defaultExpanded);

// ─── 数据派生 ────────────────────────────────────────────────────

const items = computed<SnapshotItem[]>(() => {
  if (!props.snapshot) return [];
  return Object.values(props.snapshot).sort((a, b) => a.key.localeCompare(b.key));
});

const syntheticItems = computed(() =>
  items.value.filter((it) => !!it.synthetic_source),
);

const manualOverrideKeys = computed(() => {
  if (!props.manualOverrides) return [];
  return Object.keys(props.manualOverrides).sort();
});

const overrideRows = computed(() =>
  manualOverrideKeys.value.map((k) => ({
    key: k,
    value: props.manualOverrides![k],
  })),
);

// ─── 表格列 ─────────────────────────────────────────────────────

const VALUE_TYPE_META: Record<
  string,
  { label: string; color: "default" | "info" | "success" | "warning" | "error" }
> = {
  string: { label: "string", color: "info" },
  multiline: { label: "multiline", color: "info" },
  secret: { label: "secret", color: "error" },
  file: { label: "file", color: "warning" },
  random: { label: "random", color: "success" },
  dataset: { label: "dataset", color: "default" },
};

function valueTypeMeta(t: string) {
  return VALUE_TYPE_META[t] ?? { label: t, color: "default" as const };
}

/** Secret 类型在表格里展示的"占位文本"。
 *
 *  后端 ``TestDataResolver.serialize_for_audit()`` 会把 secret 明文换成
 *  字面量字符串 ``"<secret:redacted>"``。这串文本暴露给最终用户既不友好
 *  也不直观——所以前端拿到这个 sentinel 就替换成中文锁图标。 */
const SECRET_REDACTED_SENTINEL = "<secret:redacted>";
const SECRET_DISPLAY_LABEL = "🔒 已脱敏";

function previewValue(item: SnapshotItem): string {
  if (item.value_type === "secret") {
    const v = item.value;
    if (!v || v === SECRET_REDACTED_SENTINEL) return SECRET_DISPLAY_LABEL;
    return v;
  }
  if (item.value_type === "file") {
    const sizeKB = typeof item.file_size === "number"
      ? ` · ${(item.file_size / 1024).toFixed(1)} KB`
      : "";
    return `${item.file_name ?? "(file)"}${sizeKB}`;
  }
  if (item.value_type === "dataset") {
    if (Array.isArray(item.value_json)) {
      return `[ array · ${item.value_json.length} 行 ]`;
    }
    if (item.value_json && typeof item.value_json === "object") {
      return `{ object · ${Object.keys(item.value_json).length} 字段 }`;
    }
    return "(dataset)";
  }
  const txt = item.value_text ?? "";
  return txt.length > 80 ? `${txt.slice(0, 80)}…` : txt;
}

function previewOverrideValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    if (value === SECRET_REDACTED_SENTINEL) return SECRET_DISPLAY_LABEL;
    return value.length > 80 ? `${value.slice(0, 80)}…` : value;
  }
  try {
    const txt = JSON.stringify(value);
    return txt.length > 80 ? `${txt.slice(0, 80)}…` : txt;
  } catch {
    return String(value);
  }
}

const itemColumns: DataTableColumns<SnapshotItem> = [
  {
    title: "Key",
    key: "key",
    width: 180,
    fixed: "left",
    render: (row) =>
      h("code", { class: "snapshot-panel__key" }, [row.key]),
  },
  {
    title: "类型",
    key: "value_type",
    width: 90,
    render: (row) => {
      const meta = valueTypeMeta(row.value_type);
      return h(
        NTag,
        { type: meta.color, size: "tiny", bordered: false },
        () => meta.label,
      );
    },
  },
  {
    title: "来源",
    key: "source",
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => {
      // 优先级：synthetic（AI 自造）> source_set（物料集）> 临时/无源
      if (row.synthetic_source) {
        return h(
          NTag,
          { type: "warning", size: "tiny", bordered: false },
          () => `🟡 ${row.synthetic_source}`,
        );
      }
      if (row.source_set_name) {
        return h(
          NTag,
          { type: "info", size: "tiny", bordered: false },
          () => row.source_set_name,
        );
      }
      // adhoc / manual override → 由 manualOverrides 区单独展示
      return h("span", { class: "text-tertiary" }, "—");
    },
  },
  {
    title: "值预览",
    key: "value",
    minWidth: 240,
    ellipsis: { tooltip: true },
    render: (row) => previewValue(row),
  },
  {
    title: "描述",
    key: "description",
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) => row.description ?? "—",
  },
];

const overrideColumns: DataTableColumns<{ key: string; value: unknown }> = [
  {
    title: "Key",
    key: "key",
    width: 180,
    render: (row) => h("code", { class: "snapshot-panel__key" }, [row.key]),
  },
  {
    title: "覆盖值",
    key: "value",
    minWidth: 320,
    ellipsis: { tooltip: true },
    render: (row) => previewOverrideValue(row.value),
  },
];

const paginationProps = computed<false | PaginationProps>(() =>
  items.value.length > 12
    ? { pageSize: 10, showSizePicker: false }
    : false,
);

function rowClass(row: SnapshotItem): string {
  if (row.synthetic_source) return "snapshot-panel__row--synth";
  if (row.value_type === "secret") return "snapshot-panel__row--secret";
  return "";
}
</script>

<style scoped>
.snapshot-panel {
  border: 1px solid var(--border-subtle);
}

.snapshot-panel__head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.snapshot-panel__count {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.snapshot-panel__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.snapshot-panel__synth {
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}

.snapshot-panel__synth-head {
  font-size: 12px;
  color: #b45309;
  font-weight: 600;
  margin-bottom: 6px;
}

.snapshot-panel__synth-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.snapshot-panel__synth-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
  padding: 2px 8px;
  border-radius: 999px;
}

.snapshot-panel__synth-source {
  color: rgba(180, 83, 9, 0.7);
}

.snapshot-panel__overrides,
.snapshot-panel__table {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.snapshot-panel__overrides-head,
.snapshot-panel__table-head {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.snapshot-panel__table-hint {
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.snapshot-panel__key {
  background: var(--bg-page-soft);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

:deep(.snapshot-panel__row--synth) {
  background: rgba(245, 158, 11, 0.04);
}

:deep(.snapshot-panel__row--secret) {
  background: rgba(239, 68, 68, 0.04);
}

.text-warning {
  color: var(--color-warning, #f59e0b);
}

.text-info {
  color: var(--color-info, #0ea5e9);
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
