<template>
  <!-- 物料合并预览表（Task 10.1）。
       职责：
       1. 展示 preview-merge 接口返回的合并结果（多层物料叠加后的最终值）
       2. 让用户对单个 key "✏️ 临时改写"——改写后值最高优先级，且仅本次执行生效
       3. secret 永不展示明文（只展示●●●● + "已写入"标记）；想改 secret 也允许，
          但提交后该 secret value 是明文随 ``manual_overrides`` 上传——按"用户
          自己授权本次暴露"的语义处理（与 §3.6 一致）

       提交后由父组件把 manualOverrides 一并下发给 createExecutionApi。
       本组件不负责调用任何 API，纯展示 + emit。 -->
  <div class="merge-preview">
    <div v-if="loading" class="merge-preview__loading">
      <n-spin :size="14" />
      <span class="text-sm text-tertiary ml-2">正在合并物料...</span>
    </div>

    <n-empty
      v-else-if="items.length === 0"
      size="small"
      description="无可用物料（未加载任何物料集，且未设置临时覆盖）"
      class="merge-preview__empty"
    />

    <!-- 行展开内容：展示该 key 的全部候选来源（同名 key 在多个集合里时
         非常重要——之前用户反馈"多个物料集都有 username，合并明细只展示
         一条"）。无 sources 或只有一条时不展示展开按钮。 -->
    <n-data-table
      v-else
      :columns="columns"
      :data="rowsForRender"
      :bordered="false"
      :single-line="false"
      size="small"
      :row-key="(r: PreviewRow) => r.key"
      :scroll-x="640"
      striped
      class="merge-preview__table"
    />

    <!-- 临时改写编辑弹窗 -->
    <n-modal
      v-model:show="editing.show"
      preset="card"
      :title="`临时改写 ${editing.key}`"
      style="width: 460px"
      :mask-closable="false"
    >
      <n-form label-placement="top" size="small">
        <n-form-item label="新值">
          <n-input
            v-if="editing.isSecret"
            v-model:value="editing.value"
            type="password"
            show-password-on="click"
            placeholder="留空 = 取消改写"
          />
          <n-input
            v-else
            v-model:value="editing.value"
            :type="editing.isMultiline ? 'textarea' : 'text'"
            :rows="editing.isMultiline ? 4 : 1"
            placeholder="留空 = 取消改写"
          />
        </n-form-item>
        <n-alert v-if="editing.isSecret" type="warning" size="small">
          secret 类型物料：本次提交将以明文随 ``manual_overrides`` 发送一次。
          仅本次执行生效，不会写回物料库；执行结束后清空。
        </n-alert>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button quaternary @click="editing.show = false">取消</n-button>
          <n-button
            v-if="hasOverride(editing.key)"
            type="warning"
            ghost
            @click="clearOverride(editing.key)"
          >
            <template #icon><span class="i-carbon-undo" /></template>
            移除本次改写
          </n-button>
          <n-button type="primary" @click="commitOverride">
            保存改写
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, reactive } from "vue";
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSpin,
  NTag,
  NTooltip,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { MergedItem, MergeSource } from "@/services/uiAutomation";

interface PreviewRow extends MergedItem {
  /** "manual_override" 时表示当前 key 已被用户在弹窗里临时改过 */
  override_active: boolean;
}

const SCOPE_LABEL: Record<MergeSource["scope"], string> = {
  personal: "个人",
  project: "项目",
  environment: "环境",
  loaded: "加载集",
  testcase: "用例默认",
  manual: "弹窗改写",
};

const SCOPE_COLOR: Record<
  MergeSource["scope"],
  "default" | "info" | "success" | "warning" | "error"
> = {
  personal: "default",
  project: "info",
  environment: "success",
  loaded: "warning",
  testcase: "warning",
  manual: "error",
};

const props = defineProps<{
  items: MergedItem[];
  /** 当前临时覆盖（k → v）。已 deduped，由父组件维护 */
  manualOverrides: Record<string, unknown>;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:manualOverrides", v: Record<string, unknown>): void;
}>();

const editing = reactive({
  show: false,
  key: "",
  value: "",
  isSecret: false,
  isMultiline: false,
});

const rowsForRender = computed<PreviewRow[]>(() =>
  props.items.map((it) => ({
    ...it,
    override_active: hasOverride(it.key),
  })),
);

function hasOverride(key: string): boolean {
  return Object.prototype.hasOwnProperty.call(props.manualOverrides, key);
}

function openEditor(row: PreviewRow): void {
  editing.show = true;
  editing.key = row.key;
  editing.isSecret = row.value_type === "secret" || row.has_secret_value;
  editing.isMultiline = row.value_type === "multiline";
  // 显示当前覆盖值；secret 类型不预填明文（避免泄露原值）
  const cur = props.manualOverrides[row.key];
  editing.value = editing.isSecret
    ? ""
    : cur !== undefined && cur !== null
      ? String(cur)
      : "";
}

function commitOverride(): void {
  const key = editing.key;
  const value = editing.value.trim();
  if (!value) {
    // 空值 = 取消改写（与按钮"移除本次改写"语义统一）
    clearOverride(key);
    return;
  }
  emit("update:manualOverrides", {
    ...props.manualOverrides,
    [key]: editing.value,
  });
  editing.show = false;
}

/**
 * 渲染行展开内容：把该 key 的所有候选来源平铺成小表格。
 * 行高色调：被覆盖（``overridden=true``）→ 偏淡 + 删除线；当前胜出 →
 * 绿色高亮。secret 永远 ●●●●，不会泄漏明文（与后端约束一致）。
 */
function renderSourceList(row: PreviewRow) {
  const sources = row.sources ?? [];
  if (sources.length === 0) {
    return h("div", { class: "merge-preview__source-empty" }, [
      "无溯源记录（可能是 AI 自造或 ad-hoc 数据）",
    ]);
  }
  return h(
    "div",
    { class: "merge-preview__sources" },
    [
      h(
        "div",
        { class: "merge-preview__sources-head" },
        `共 ${sources.length} 条来源（最后一条为合并胜出值，前面的会被覆盖）`,
      ),
      h(
        "table",
        { class: "merge-preview__sources-table" },
        [
          h("thead", null, [
            h("tr", null, [
              h("th", null, "#"),
              h("th", null, "层级"),
              h("th", null, "物料集"),
              h("th", null, "值（本来源）"),
              h("th", null, "状态"),
            ]),
          ]),
          h(
            "tbody",
            null,
            sources.map((src, idx) =>
              h(
                "tr",
                {
                  class: [
                    "merge-preview__sources-row",
                    src.overridden ? "is-overridden" : "is-winner",
                  ],
                },
                [
                  h("td", { class: "merge-preview__source-idx" }, String(idx + 1)),
                  h("td", null, [
                    h(
                      NTag,
                      {
                        size: "tiny",
                        type: SCOPE_COLOR[src.scope] ?? "default",
                        bordered: false,
                      },
                      () => SCOPE_LABEL[src.scope] ?? src.scope,
                    ),
                  ]),
                  h(
                    "td",
                    { class: "merge-preview__source-name" },
                    src.set_name,
                  ),
                  h(
                    "td",
                    { class: "merge-preview__source-value" },
                    src.has_secret_value ? "●●●●" : src.display_value || "—",
                  ),
                  h("td", null, [
                    src.overridden
                      ? h(
                          NTag,
                          {
                            size: "tiny",
                            type: "default",
                            bordered: false,
                          },
                          () => "已被覆盖",
                        )
                      : h(
                          NTag,
                          {
                            size: "tiny",
                            type: "success",
                            bordered: false,
                          },
                          () => "✓ 当前胜出",
                        ),
                  ]),
                ],
              ),
            ),
          ),
        ],
      ),
    ],
  );
}

function clearOverride(key: string): void {
  if (!hasOverride(key)) {
    editing.show = false;
    return;
  }
  const next = { ...props.manualOverrides };
  delete next[key];
  emit("update:manualOverrides", next);
  editing.show = false;
}

const VALUE_TYPE_LABELS: Record<string, { label: string; type: "default" | "info" | "success" | "warning" }> = {
  string: { label: "文本", type: "default" },
  multiline: { label: "多行", type: "info" },
  secret: { label: "凭据", type: "warning" },
  file: { label: "文件", type: "success" },
  random: { label: "随机", type: "info" },
  dataset: { label: "数据组", type: "default" },
};

const columns: DataTableColumns<PreviewRow> = [
  // expandable 列：仅当该 key 有多于 1 条来源（说明被覆盖过）才允许展开。
  // 单一来源 / 无来源时不显示箭头，避免无意义点击。
  {
    type: "expand",
    expandable: (row) => (row.sources?.length ?? 0) > 1,
    renderExpand: (row) => renderSourceList(row),
  },
  {
    title: "Key",
    key: "key",
    // 列宽收紧（160→140）+ scroll-x=640 启用横滚：原来在 720 宽 modal 里会
    // 撑出底部，现在 modal 加到 880 + 表格 ``:scroll-x="640"`` 让列宽不够时
    // 横向滚动而不是撑爆容器。详见 modal 顶部 ``maxWidth: calc(100vw - 32px)``。
    width: 140,
    ellipsis: { tooltip: true },
    render: (row) =>
      h("code", { class: "merge-preview__key" }, row.key),
  },
  {
    title: "类型",
    key: "value_type",
    width: 70,
    render: (row) => {
      const meta = VALUE_TYPE_LABELS[row.value_type] ?? VALUE_TYPE_LABELS.string;
      return h(NTag, { size: "tiny", type: meta.type, bordered: false }, () => meta.label);
    },
  },
  {
    title: "值",
    key: "display_value",
    minWidth: 180,
    ellipsis: { tooltip: true },
    render: (row) => {
      const overrideCount = (row.sources?.length ?? 0) - 1;
      const conflictBadge =
        overrideCount > 0
          ? h(
              NTooltip,
              { trigger: "hover", placement: "top" },
              {
                trigger: () =>
                  h(
                    NTag,
                    {
                      size: "tiny",
                      type: "warning",
                      bordered: false,
                      class: "ml-1",
                    },
                    () => `覆盖 ${overrideCount} 条`,
                  ),
                default: () =>
                  `该 key 在 ${overrideCount + 1} 个物料集中都有定义，已展开行可看全部候选；当前显示的是合并后胜出值。`,
              },
            )
          : null;

      if (row.override_active) {
        // 用户已临时改过：用绿色徽章 + "已改写" 标识
        return h("div", { class: "flex items-center gap-1" }, [
          h(
            "span",
            { class: "merge-preview__value merge-preview__value--override" },
            row.value_type === "secret" ? "●●●●" : "（已临时改写）",
          ),
          h(
            NTag,
            { size: "tiny", type: "success", bordered: false },
            () => "本次覆盖",
          ),
          conflictBadge,
        ]);
      }
      if (row.synthetic_source) {
        return h("span", { class: "merge-preview__value merge-preview__value--synth" }, [
          row.display_value,
          h(
            NTag,
            { size: "tiny", type: "warning", bordered: false, class: "ml-1" },
            () => "AI 自造",
          ),
          conflictBadge,
        ]);
      }
      return h("span", { class: "flex items-center" }, [
        h("span", { class: "merge-preview__value" }, row.display_value),
        conflictBadge,
      ]);
    },
  },
  {
    title: "说明",
    key: "description",
    minWidth: 140,
    ellipsis: { tooltip: true },
    render: (row) => row.description || "—",
  },
  {
    title: "操作",
    key: "actions",
    width: 80,
    align: "center",
    titleAlign: "center",
    render: (row) =>
      h(
        NButton,
        {
          size: "tiny",
          quaternary: true,
          type: row.override_active ? "warning" : "primary",
          onClick: () => openEditor(row),
        },
        {
          icon: () =>
            h("span", { class: row.override_active ? "i-carbon-edit" : "i-carbon-pen" }),
          default: () => (row.override_active ? "调整" : "改值"),
        },
      ),
  },
];
</script>

<style scoped>
.merge-preview__loading,
.merge-preview__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: var(--bg-page);
  border-radius: var(--radius-sm);
}

/* 关键样式：让 n-data-table 的滚动容器自身宽度不超过父级，由 ``:scroll-x``
   触发横向滚动条，而不是把外面的 modal / collapse 撑出去（用户反馈"明细
   列表表单宽度超出底部选项窗"就是这个症状）。 */
.merge-preview__table {
  width: 100%;
  max-width: 100%;
}

/* 行展开里的"全部候选来源"小表格 —— 用 native ``<table>`` 而非嵌套 n-data-table，
   原因：嵌套 n-data-table 在 expand 行里布局会无视父级宽度，且引入第二套
   滚动条；native table 简单、视觉一致、宽度跟随父行。 */
.merge-preview__sources {
  padding: 8px 12px 4px;
  background: var(--bg-page-soft);
  border-radius: var(--radius-sm);
}

.merge-preview__sources-head {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
}

.merge-preview__sources-table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.merge-preview__sources-table th,
.merge-preview__sources-table td {
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
  vertical-align: middle;
}

.merge-preview__sources-table th {
  font-weight: 500;
  color: var(--text-tertiary);
  background: transparent;
  white-space: nowrap;
}

.merge-preview__sources-row.is-overridden {
  color: var(--text-tertiary);
}

.merge-preview__sources-row.is-overridden .merge-preview__source-value {
  text-decoration: line-through;
  text-decoration-color: var(--text-tertiary);
}

.merge-preview__sources-row.is-winner {
  background: rgba(22, 163, 74, 0.06);
}

.merge-preview__source-idx {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  width: 28px;
  color: var(--text-tertiary);
}

.merge-preview__source-name {
  font-weight: 500;
  word-break: break-all;
}

.merge-preview__source-value {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  word-break: break-all;
}

.merge-preview__source-empty {
  padding: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  border-radius: var(--radius-sm);
}

.merge-preview__key {
  display: inline-block;
  padding: 1px 6px;
  background: var(--bg-page-soft);
  color: var(--text-secondary);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.merge-preview__value {
  font-size: 12px;
  color: var(--text-primary);
}

.merge-preview__value--override {
  color: var(--color-success, #16a34a);
  font-weight: 500;
}

.merge-preview__value--synth {
  color: var(--color-warning, #b45309);
}

.text-tertiary {
  color: var(--text-tertiary);
}
</style>
