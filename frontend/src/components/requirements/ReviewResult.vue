<template>
  <div class="review-result">
    <div class="review-result__header">
      <div class="review-result__title-block">
        <span class="i-carbon-checkmark-outline review-result__title-icon" :class="statusColor" />
        <span class="review-result__title">AI 评审结果</span>
        <n-tag v-if="review.status === 'completed'" type="success" size="small" :bordered="false">已完成</n-tag>
        <n-tag v-else-if="review.status === 'failed'" type="error" size="small" :bordered="false">失败</n-tag>
        <n-tag v-else type="warning" size="small" :bordered="false">{{ review.status }}</n-tag>
      </div>
      <span class="review-result__time">{{ formatDate(review.created_at) }}</span>
    </div>

    <template v-if="review.status === 'completed'">
      <!-- 总体评分 -->
      <div class="review-overall">
        <div class="review-overall__score">
          <n-progress
            type="circle"
            :percentage="review.overall_score ?? 0"
            :color="scoreColor"
            :rail-color="scoreRailColor"
            :stroke-width="10"
            :indicator-text-color="scoreColor"
            style="width: 96px"
          >
            <span class="review-overall__num">{{ review.overall_score?.toFixed(0) }}</span>
          </n-progress>
          <div class="review-overall__label">总体评分</div>
        </div>
        <div class="review-overall__summary">
          <div class="review-overall__heading">
            <span class="i-carbon-bot text-brand mr-1" />
            综合评价
          </div>
          <p v-if="review.summary" class="review-overall__summary-text">
            {{ review.summary }}
          </p>
          <div class="review-overall__meta">
            <span v-if="review.model_used" class="review-overall__meta-item">
              <span class="i-carbon-machine-learning-model mr-1" />{{ review.model_used }}
            </span>
            <span v-if="review.review_time_ms" class="review-overall__meta-item">
              <span class="i-carbon-time mr-1" />{{ (review.review_time_ms / 1000).toFixed(1) }}s
            </span>
            <span v-if="review.reviewer_name" class="review-overall__meta-item">
              <span class="i-carbon-user mr-1" />{{ review.reviewer_name }}
            </span>
          </div>
        </div>
      </div>

      <!-- 维度评分（中文 + 高亮） -->
      <div v-if="dimensionEntries.length > 0" class="dim-section">
        <div class="dim-section__title">
          <span class="i-carbon-chart-radar text-brand mr-1" />
          维度评分
        </div>
        <div class="dim-grid">
          <div
            v-for="[key, dim] in dimensionEntries"
            :key="key"
            class="dim-card"
            :class="[
              `dim-card--${tier(dim.score)}`,
              { 'is-expanded': expandedDimensions[key] },
            ]"
            @click="toggleDimension(key)"
          >
            <div class="dim-card__head">
              <div class="dim-card__name">{{ dimensionLabel(key) }}</div>
              <div class="dim-card__score">
                {{ dim.score }}
                <span class="dim-card__score-suffix">分</span>
              </div>
            </div>
            <div class="dim-bar">
              <div class="dim-bar__fill" :style="{ width: dim.score + '%' }" />
            </div>
            <div v-if="dim.comment" class="dim-card__comment">{{ dim.comment }}</div>
            <div v-if="dim.comment && isCommentLong(dim.comment)" class="dim-card__toggle">
              <span :class="expandedDimensions[key] ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
              {{ expandedDimensions[key] ? '收起' : '展开全部' }}
            </div>
          </div>
        </div>
      </div>

      <!-- 问题列表 -->
      <div v-if="review.issues && review.issues.length > 0" class="issue-section">
        <div class="dim-section__title">
          <span class="i-carbon-warning-alt text-brand mr-1" />
          发现问题（{{ review.issues.length }}）
        </div>
        <n-collapse>
          <n-collapse-item
            v-for="(issue, idx) in review.issues"
            :key="idx"
            :title="issue.description"
          >
            <template #header-extra>
              <n-tag :type="severityType(issue.severity)" size="small" :bordered="false">
                {{ severityLabel(issue.severity) }}
              </n-tag>
            </template>
            <div class="issue-detail">
              <div v-if="issue.category">
                <span class="issue-detail__label">分类：</span>{{ issue.category }}
              </div>
              <div v-if="issue.location">
                <span class="issue-detail__label">位置：</span>{{ issue.location }}
              </div>
              <div v-if="issue.suggestion">
                <span class="issue-detail__label">建议：</span>{{ issue.suggestion }}
              </div>
            </div>
          </n-collapse-item>
        </n-collapse>
      </div>
    </template>

    <template v-else-if="review.status === 'failed'">
      <n-text depth="3">评审执行失败，请重试。</n-text>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from "vue";
import { NTag, NText, NProgress, NCollapse, NCollapseItem } from "naive-ui";
import type { ReviewInfo, ReviewDimensionScore } from "@/services/requirements";

const props = defineProps<{ review: ReviewInfo }>();

const expandedDimensions = reactive<Record<string, boolean>>({});

function toggleDimension(key: string) {
  expandedDimensions[key] = !expandedDimensions[key];
}

function isCommentLong(text: string) {
  return text.length > 60;
}

const statusColor = computed(() => {
  if (props.review.status === "completed") return "text-green-500";
  if (props.review.status === "failed") return "text-red-500";
  return "text-yellow-500";
});

const scoreColor = computed(() => {
  const s = props.review.overall_score ?? 0;
  if (s >= 80) return "#18a058";
  if (s >= 60) return "#f0a020";
  return "#d03050";
});

const scoreRailColor = computed(() => {
  const s = props.review.overall_score ?? 0;
  if (s >= 80) return "rgba(24,160,88,0.15)";
  if (s >= 60) return "rgba(240,160,32,0.15)";
  return "rgba(208,48,80,0.15)";
});

const dimensionEntries = computed<[string, ReviewDimensionScore][]>(() => {
  if (!props.review.dimensions) return [];
  return Object.entries(props.review.dimensions) as [string, ReviewDimensionScore][];
});

const dimensionMap: Record<string, string> = {
  completeness: "完整性",
  clarity: "清晰性",
  consistency: "一致性",
  testability: "可测试性",
  feasibility: "可行性",
  accuracy: "准确性",
  structure: "结构性",
  scope: "范围明确性",
};

function dimensionLabel(key: string) {
  return dimensionMap[key] || key;
}

function tier(score: number): "high" | "mid" | "low" {
  if (score >= 80) return "high";
  if (score >= 60) return "mid";
  return "low";
}

function severityType(severity: string) {
  if (severity === "high") return "error" as const;
  if (severity === "medium") return "warning" as const;
  return "info" as const;
}

function severityLabel(severity: string) {
  if (severity === "high") return "高";
  if (severity === "medium") return "中";
  return "低";
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString("zh-CN");
}
</script>

<style scoped>
.review-result {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}

.review-result__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.review-result__title-block {
  display: flex;
  align-items: center;
  gap: 8px;
}

.review-result__title-icon {
  font-size: 18px;
}

.review-result__title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
}

.review-result__time {
  font-size: 12px;
  color: var(--text-tertiary);
}

.review-overall {
  display: flex;
  gap: 18px;
  align-items: center;
  padding: 14px;
  background: var(--bg-page-soft);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.review-overall__score {
  text-align: center;
  flex-shrink: 0;
}

.review-overall__num {
  font-size: 22px;
  font-weight: 700;
}

.review-overall__label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}

.review-overall__summary {
  flex: 1;
  min-width: 0;
}

.review-overall__heading {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
}

.review-overall__summary-text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
  margin: 0 0 8px;
}

.review-overall__meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  flex-wrap: wrap;
}

.review-overall__meta-item {
  display: inline-flex;
  align-items: center;
}

.dim-section,
.issue-section {
  margin-top: 14px;
}

.dim-section__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
}

.dim-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
}

.dim-card {
  border-radius: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
  cursor: pointer;
  transition: transform var(--duration-fast) var(--easing-standard),
    box-shadow var(--duration-fast) var(--easing-standard);
}

.dim-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.dim-card.is-expanded {
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.08));
}

.dim-card--high {
  border-color: rgba(24, 160, 88, 0.4);
  background: rgba(24, 160, 88, 0.04);
}

.dim-card--mid {
  border-color: rgba(240, 160, 32, 0.4);
  background: rgba(240, 160, 32, 0.04);
}

.dim-card--low {
  border-color: rgba(208, 48, 80, 0.4);
  background: rgba(208, 48, 80, 0.04);
}

.dim-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.dim-card__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.dim-card__score {
  font-size: 18px;
  font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, monospace;
}

.dim-card--high .dim-card__score {
  color: #18a058;
}

.dim-card--mid .dim-card__score {
  color: #f0a020;
}

.dim-card--low .dim-card__score {
  color: #d03050;
}

.dim-card__score-suffix {
  font-size: 12px;
  font-weight: 500;
  margin-left: 1px;
  opacity: 0.7;
}

.dim-bar {
  height: 4px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 999px;
  overflow: hidden;
  margin: 8px 0;
}

.dim-card--high .dim-bar__fill {
  background: linear-gradient(90deg, #34d399, #18a058);
}

.dim-card--mid .dim-bar__fill {
  background: linear-gradient(90deg, #fbbf24, #f0a020);
}

.dim-card--low .dim-bar__fill {
  background: linear-gradient(90deg, #fb7185, #d03050);
}

.dim-bar__fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.6s ease;
}

.dim-card__comment {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-tertiary);
  margin-top: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
  transition: -webkit-line-clamp 0.2s ease;
}

.dim-card.is-expanded .dim-card__comment {
  display: block;
  -webkit-line-clamp: unset;
  overflow: visible;
}

.dim-card__toggle {
  margin-top: 6px;
  font-size: 11px;
  color: var(--brand-primary);
  display: inline-flex;
  align-items: center;
  gap: 2px;
  user-select: none;
}

.dim-card__toggle:hover {
  text-decoration: underline;
}

.issue-detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.issue-detail__label {
  color: var(--text-tertiary);
  margin-right: 4px;
}

.text-brand {
  color: var(--brand-primary);
}
</style>
