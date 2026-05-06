<template>
  <!-- 推荐物料集多选清单（Task 10.1）。
       数据来自 ``GET /api/projects/{id}/test-data/recommend``，分四类：
       项目默认 / 用例默认 / 我的物料 / 常用 —— reason_code 决定徽章颜色。

       UX 约定：
       - 项目默认 + 用例默认会被 ExecuteDialog 在打开时**自动勾选**，用户能看到
         哪些是"被建议而且我已经在加载"，哪些是"建议但你还没勾"
       - 个人物料和常用是辅助，默认不勾——避免手滑加载到非本意的物料
       - 列表为空时显示"暂无推荐"灰态而非整段消失，保持容器尺寸稳定 -->
  <div class="data-rec">
    <div v-if="loading" class="data-rec__loading">
      <n-spin :size="14" />
      <span class="text-sm text-tertiary ml-2">正在分析推荐物料...</span>
    </div>

    <div v-else-if="recommendations.length === 0" class="data-rec__empty">
      <span class="i-carbon-information text-tertiary" />
      <span class="text-sm text-tertiary">暂无推荐物料集（项目还没设置默认 / 没有用例默认）</span>
    </div>

    <n-checkbox-group
      v-else
      :value="selected"
      @update:value="handleChange"
    >
      <div class="data-rec__list">
        <div
          v-for="rec in sortedRecs"
          :key="rec.set.id"
          class="data-rec__item"
          :class="{ 'data-rec__item--checked': selected.includes(rec.set.id) }"
        >
          <n-checkbox :value="rec.set.id" class="data-rec__checkbox">
            <div class="data-rec__row">
              <span class="data-rec__name">{{ rec.set.name }}</span>
              <n-tag
                size="tiny"
                :type="reasonMeta(rec.reason_code).color"
                :bordered="false"
                class="data-rec__reason"
              >
                <template #icon>
                  <span :class="reasonMeta(rec.reason_code).icon" />
                </template>
                {{ reasonMeta(rec.reason_code).label }}
              </n-tag>
              <n-tag
                v-if="rec.set.scope === 'personal'"
                size="tiny"
                type="default"
                bordered
                class="data-rec__scope"
              >
                个人
              </n-tag>
            </div>
            <div v-if="rec.set.description" class="data-rec__desc">
              {{ rec.set.description }}
            </div>
          </n-checkbox>
        </div>
      </div>
    </n-checkbox-group>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NCheckbox, NCheckboxGroup, NSpin, NTag } from "naive-ui";
import {
  RECOMMEND_REASON_META,
  type RecommendedSet,
  type RecommendReasonCode,
} from "@/services/testData";

const props = defineProps<{
  recommendations: RecommendedSet[];
  /** 当前已勾选的 set_id 列表（v-model 风格） */
  selected: string[];
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:selected", ids: string[]): void;
}>();

/**
 * 排序优先级：环境默认 > 项目默认 > 用例默认 > 个人 > 常用。同优先级按
 * set.name 字典序，反复打开弹窗时顺序稳定。
 */
const REASON_ORDER: Record<RecommendReasonCode, number> = {
  env_default: 0,
  project_default: 1,
  testcase_default: 2,
  personal: 3,
  popular: 4,
};

const sortedRecs = computed(() => {
  return [...props.recommendations].sort((a, b) => {
    const oa = REASON_ORDER[a.reason_code] ?? 99;
    const ob = REASON_ORDER[b.reason_code] ?? 99;
    if (oa !== ob) return oa - ob;
    return a.set.name.localeCompare(b.set.name, "zh-CN");
  });
});

function reasonMeta(code: RecommendReasonCode) {
  return RECOMMEND_REASON_META[code] ?? RECOMMEND_REASON_META.popular;
}

function handleChange(ids: (string | number)[]) {
  emit("update:selected", ids.map(String));
}
</script>

<style scoped>
.data-rec {
  min-height: 36px;
}

.data-rec__loading,
.data-rec__empty {
  display: flex;
  align-items: center;
  padding: 12px;
  background: var(--bg-page);
  border-radius: var(--radius-sm);
}

.data-rec__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.data-rec__item {
  padding: 8px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  transition: background 0.12s ease, border-color 0.12s ease;
}

.data-rec__item--checked {
  border-color: var(--brand-primary-border);
  background: var(--brand-primary-soft);
}

.data-rec__checkbox {
  align-items: flex-start;
  width: 100%;
}

.data-rec__row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.data-rec__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.data-rec__reason {
  font-size: 11px;
}

.data-rec__scope {
  font-size: 11px;
  color: var(--text-tertiary);
}

.data-rec__desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.45;
}

.text-tertiary {
  color: var(--text-tertiary);
}
</style>
