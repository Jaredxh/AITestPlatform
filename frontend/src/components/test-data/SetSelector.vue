<template>
  <div class="set-selector">
    <!-- 已选物料集 tag 展示 + 排序 + 移除 + 点击查看明细 -->
    <draggable-chips
      v-if="selectedSets.length > 0"
      :sets="selectedSets"
      :disabled="disabled"
      class="set-selector__chips"
      @reorder="handleReorder"
      @remove="handleRemove"
      @select="handleSelect"
    />
    <div v-else class="set-selector__empty">
      <span class="i-carbon-data-blob" />
      暂未绑定任何物料集；执行时将回落到项目默认 / 推荐值
    </div>

    <!-- 添加按钮（弹窗选择）。
         **重要**：``width`` 设为定值 (420px) 而不是 ``"trigger"``。原来用
         ``"trigger"`` 时 popselect 宽 = 触发按钮宽（~120px），导致物料集名
         字被严重截断，用户只看见 group label 的"项目"/"常用" 这种标题字
         （2026-05 用户验收反馈"名称展示不全只看到大标题"）。 -->
    <n-popselect
      v-model:value="popSelection"
      :options="popOptions"
      multiple
      scrollable
      trigger="click"
      :render-label="renderPopLabel"
      :width="420"
      :disabled="disabled"
      placement="bottom-start"
      @update:show="handlePopShow"
    >
      <n-button
        size="small"
        :disabled="disabled"
        class="set-selector__add-btn"
      >
        <template #icon><span class="i-carbon-add" /></template>
        添加物料集
      </n-button>
    </n-popselect>

    <n-spin v-if="loading" size="small" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, h, onMounted, defineComponent } from "vue";
import type { PropType } from "vue";
import {
  NButton,
  NPopselect,
  NSpin,
  NTag,
  NTooltip,
  useMessage,
} from "naive-ui";
import type { SelectOption } from "naive-ui";

import {
  listSetsApi,
  recommendSetsApi,
  SCOPE_META,
  RECOMMEND_REASON_META,
} from "@/services/testData";
import type {
  TestDataSet,
  DataSetScope,
  RecommendedSet,
  RecommendReasonCode,
} from "@/services/testData";

/**
 * SetSelector — 物料集多选组件。
 *
 * UI 约定：
 * - 已选物料集作为可拖拽的 chip 横排展示；顺序就是加载优先级（前 ＝ 低）
 * - 点「添加物料集」打开 n-popselect，按 scope 分组（项目 / 环境 / 个人）
 * - 推荐项目顶部优先展示，带 reason tag（项目默认 / 用例默认 / 我的 / 常用）
 *
 * props.modelValue：已选 set id 列表（有序）。
 * props.testcaseIds：可选；用于让 recommend 端点带上用例上下文。
 */
const props = defineProps<{
  /** 当前项目 id；切换项目要重拉列表 */
  projectId: string;
  /** 已选物料集 id 列表（有序） */
  modelValue: string[];
  /** 可选：当前绑定的用例 id，用来让推荐更精准 */
  testcaseIds?: string[];
  disabled?: boolean;
  /**
   * 可选：限定只允许选某个 scope。默认允许全部 3 个 scope。
   * environment 绑定页面通常会传 `["project","environment"]`。
   */
  allowedScopes?: DataSetScope[];
}>();

const emit = defineEmits<{
  "update:modelValue": [ids: string[]];
  /**
   * 用户点击已选物料集 chip 触发；父组件可以借此展开明细面板（典型场景：
   * UI 环境编辑里默认物料 tab 下方加一块"该物料集明细"列表）。
   *
   * 不通过 v-model 是因为这是一次性"用户点了一下"的交互信号，而不是受控
   * 的"当前选中项"——在某些父组件场景里可能并不需要响应。
   */
  "click-set": [set: TestDataSet];
}>();

const message = useMessage();

const allSets = ref<TestDataSet[]>([]);
const recommended = ref<RecommendedSet[]>([]);
const loading = ref(false);

const allowedScopeSet = computed<Set<DataSetScope>>(() => {
  if (!props.allowedScopes || props.allowedScopes.length === 0) {
    return new Set<DataSetScope>(["project", "environment", "personal"]);
  }
  return new Set(props.allowedScopes);
});

/** id → Set 快速索引（用于把 modelValue 的 id 反向映射回完整对象） */
const setById = computed(() => {
  const m = new Map<string, TestDataSet>();
  for (const s of allSets.value) m.set(s.id, s);
  return m;
});

/** 已选物料集（按 modelValue 顺序；未知 id 会被过滤掉并静默上报父组件） */
const selectedSets = computed<TestDataSet[]>(() => {
  const result: TestDataSet[] = [];
  for (const id of props.modelValue) {
    const s = setById.value.get(id);
    if (s) result.push(s);
  }
  return result;
});

/** recommend_reason_code 查表：id → reason_code */
const recommendReasonById = computed<Map<string, RecommendReasonCode>>(() => {
  const m = new Map<string, RecommendReasonCode>();
  for (const r of recommended.value) m.set(r.set.id, r.reason_code);
  return m;
});

async function fetchAll() {
  loading.value = true;
  try {
    // 拉"全部可见物料集"用作选择池；分页 200 已经够 edge case
    const res = await listSetsApi(props.projectId, { page: 1, page_size: 200 });
    if (res.success) {
      allSets.value = res.data.items.filter((s) =>
        allowedScopeSet.value.has(s.scope),
      );
    }
    // 推荐接口失败不阻塞选择池
    try {
      const recRes = await recommendSetsApi(props.projectId, {
        testcase_ids: props.testcaseIds,
        top_n: 20,
      });
      if (recRes.success) {
        recommended.value = recRes.data.items.filter((r) =>
          allowedScopeSet.value.has(r.set.scope as DataSetScope),
        );
      }
    } catch {
      /* ignore */
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : "加载物料集失败");
  } finally {
    loading.value = false;
  }
}

onMounted(fetchAll);

watch(
  () => [props.projectId, JSON.stringify(props.testcaseIds ?? []), JSON.stringify(props.allowedScopes ?? [])],
  () => {
    fetchAll();
  },
);

// ─── Popselect 选择状态 ──────────────────────────────────────────────

/** popselect 用的选项列表，按 scope 分组，推荐项置顶 */
const popOptions = computed<SelectOption[]>(() => {
  const opts: SelectOption[] = [];
  // 顶部：推荐项（去重，保持 recommend 顺序）
  const pushedRec = new Set<string>();
  if (recommended.value.length > 0) {
    opts.push({ type: "group", label: "建议加载", key: "__rec__", children: [] } as SelectOption);
    const recChildren = opts[opts.length - 1].children as SelectOption[];
    for (const r of recommended.value) {
      if (pushedRec.has(r.set.id)) continue;
      pushedRec.add(r.set.id);
      recChildren.push({
        label: r.set.name,
        value: r.set.id,
      });
    }
  }
  // 按 scope 分组
  for (const scope of ["project", "environment", "personal"] as const) {
    if (!allowedScopeSet.value.has(scope)) continue;
    const children: SelectOption[] = allSets.value
      .filter((s) => s.scope === scope)
      .map((s) => ({ label: s.name, value: s.id }));
    if (children.length === 0) continue;
    opts.push({
      type: "group",
      label: SCOPE_META[scope].label,
      key: `scope-${scope}`,
      children,
    } as SelectOption);
  }
  return opts;
});

const popSelection = ref<string[]>([]);

function handlePopShow(shown: boolean) {
  if (shown) {
    popSelection.value = [...props.modelValue];
  } else {
    // 关闭 popselect 时把最终选择同步到父组件
    if (JSON.stringify(popSelection.value) === JSON.stringify(props.modelValue)) {
      return;
    }
    // 保持"已选集里原有的顺序"，把新增项 append 到末尾
    const existing = props.modelValue.filter((id) =>
      popSelection.value.includes(id),
    );
    const newlyAdded = popSelection.value.filter(
      (id) => !props.modelValue.includes(id),
    );
    emit("update:modelValue", [...existing, ...newlyAdded]);
  }
}

function renderPopLabel(option: SelectOption) {
  if (option.type === "group") {
    return h(
      "span",
      { style: "color:var(--text-tertiary);font-weight:600;font-size:12px;" },
      option.label as string,
    );
  }
  const setId = option.value as string;
  const s = setById.value.get(setId);
  const reason = recommendReasonById.value.get(setId);
  const reasonMeta = reason ? RECOMMEND_REASON_META[reason] : null;
  return h(
    "div",
    { style: "display:flex;align-items:center;gap:8px;min-width:0;" },
    [
      h("span", {
        class: s ? SCOPE_META[s.scope as DataSetScope].icon : "i-carbon-data-blob",
        style: "color:var(--brand-primary);flex-shrink:0;",
      }),
      h(
        "span",
        {
          style:
            "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;",
        },
        option.label as string,
      ),
      reasonMeta
        ? h(
            NTag,
            {
              size: "tiny",
              type: reasonMeta.color,
              bordered: false,
              style: "flex-shrink:0;",
            },
            { default: () => reasonMeta.label },
          )
        : null,
      s
        ? h(
            "span",
            { style: "color:var(--text-tertiary);font-size:11px;flex-shrink:0;" },
            `${s.item_count} 项`,
          )
        : null,
    ].filter(Boolean),
  );
}

// ─── Chip 增删排序 ────────────────────────────────────────────────────

function handleReorder(newOrder: string[]) {
  emit("update:modelValue", newOrder);
}

function handleRemove(id: string) {
  emit(
    "update:modelValue",
    props.modelValue.filter((v) => v !== id),
  );
}

function handleSelect(id: string) {
  const s = setById.value.get(id);
  if (s) emit("click-set", s);
}

// ─── 内联子组件：DraggableChips ──────────────────────────────────────

const DraggableChips = defineComponent({
  name: "DraggableChips",
  props: {
    sets: {
      type: Array as PropType<TestDataSet[]>,
      required: true,
    },
    disabled: Boolean,
  },
  emits: ["reorder", "remove", "select"],
  setup(p, { emit: childEmit }) {
    const dragIndex = ref<number | null>(null);
    const overIndex = ref<number | null>(null);
    // 区分"拖拽" vs "点击"：drag 触发 dragstart 后忽略 click；不到 5px 位移 +
    // 没有发生过 drop 才算 click。
    let dragHappened = false;

    function onDragStart(i: number, ev: DragEvent) {
      if (p.disabled) return;
      dragIndex.value = i;
      dragHappened = false;
      ev.dataTransfer?.setData("text/plain", String(i));
      ev.dataTransfer!.effectAllowed = "move";
    }
    function onDragOver(i: number, ev: DragEvent) {
      if (p.disabled || dragIndex.value === null) return;
      ev.preventDefault();
      overIndex.value = i;
      dragHappened = true;
    }
    function onDrop(i: number) {
      dragHappened = true;
      if (p.disabled || dragIndex.value === null || dragIndex.value === i) {
        dragIndex.value = null;
        overIndex.value = null;
        return;
      }
      const newList = p.sets.map((s) => s.id);
      const [moved] = newList.splice(dragIndex.value, 1);
      newList.splice(i, 0, moved);
      childEmit("reorder", newList);
      dragIndex.value = null;
      overIndex.value = null;
    }
    function onDragEnd() {
      dragIndex.value = null;
      overIndex.value = null;
    }
    function onChipClick(id: string) {
      // 一次"拖动后释放"也会触发 click，所以根据 dragHappened 标志过滤
      if (dragHappened) {
        dragHappened = false;
        return;
      }
      childEmit("select", id);
    }

    return () =>
      h(
        "div",
        { class: "set-selector__chip-row" },
        p.sets.map((s, i) =>
          h(
            NTooltip,
            { placement: "top", disabled: p.sets.length < 2 },
            {
              trigger: () =>
                h(
                  "div",
                  {
                    class: [
                      "set-selector__chip",
                      {
                        "set-selector__chip--disabled": p.disabled,
                        "set-selector__chip--dragover": overIndex.value === i,
                      },
                    ],
                    draggable: !p.disabled,
                    onDragstart: (ev: DragEvent) => onDragStart(i, ev),
                    onDragover: (ev: DragEvent) => onDragOver(i, ev),
                    onDrop: () => onDrop(i),
                    onDragend: onDragEnd,
                    onClick: () => onChipClick(s.id),
                  },
                  [
                    h("span", {
                      class: [
                        SCOPE_META[s.scope as DataSetScope].icon,
                        "set-selector__chip-icon",
                      ],
                    }),
                    h("span", { class: "set-selector__chip-name" }, s.name),
                    h(
                      "span",
                      { class: "set-selector__chip-count" },
                      `${s.item_count}`,
                    ),
                    h(
                      "button",
                      {
                        type: "button",
                        class: "set-selector__chip-close",
                        disabled: p.disabled,
                        onClick: (ev: Event) => {
                          ev.stopPropagation();
                          childEmit("remove", s.id);
                        },
                      },
                      [h("span", { class: "i-carbon-close" })],
                    ),
                  ],
                ),
              default: () =>
                `优先级 ${i + 1} · ${SCOPE_META[s.scope as DataSetScope].label}${
                  s.is_default ? " · 项目默认" : ""
                }（点击查看明细 / 拖拽调整顺序）`,
            },
          ),
        ),
      );
  },
});
</script>

<style scoped>
.set-selector {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.set-selector__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:deep(.set-selector__chip-row) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

:deep(.set-selector__chip) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  border-radius: 999px;
  background: var(--brand-gradient-soft);
  border: 1px solid var(--border-subtle);
  font-size: 12.5px;
  color: var(--text-primary);
  cursor: grab;
  user-select: none;
  transition: border-color var(--duration-fast) var(--easing-standard);
  max-width: 260px;
  min-width: 0;
}

:deep(.set-selector__chip:hover) {
  border-color: var(--brand-primary-border);
}

:deep(.set-selector__chip--disabled) {
  cursor: not-allowed;
  opacity: 0.6;
}

:deep(.set-selector__chip--dragover) {
  border-color: var(--brand-primary);
  background: var(--brand-gradient-strong, var(--brand-gradient-soft));
  box-shadow: 0 0 0 2px var(--brand-primary-shadow, rgba(0, 0, 0, 0.1));
}

:deep(.set-selector__chip-icon) {
  color: var(--brand-primary);
  font-size: 14px;
}

:deep(.set-selector__chip-name) {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.set-selector__chip-count) {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 0 5px;
  border-radius: 8px;
  background: var(--bg-card);
}

:deep(.set-selector__chip-close) {
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 50%;
  color: var(--text-tertiary);
  cursor: pointer;
}

:deep(.set-selector__chip-close:hover:not(:disabled)) {
  background: var(--bg-active);
  color: var(--error-color, #d03050);
}

.set-selector__empty {
  padding: 10px 12px;
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  font-size: 12.5px;
  color: var(--text-tertiary);
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-subtle, var(--bg-card));
}

.set-selector__empty > span:first-child {
  font-size: 14px;
  color: var(--text-tertiary);
}

.set-selector__add-btn {
  align-self: flex-start;
}
</style>
