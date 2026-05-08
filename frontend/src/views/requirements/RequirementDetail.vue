<template>
  <div>
    <page-header
      :title="document?.filename || '需求文档详情'"
      subtitle="查看文档内容与历史评审记录"
      icon="i-carbon-document"
      back
      @back="router.back()"
    >
      <template v-if="document" #title-extra>
        <span
          class="review-pill"
          :class="`review-pill--${document.review_status}`"
        >
          <span :class="reviewStatusIcon(document.review_status)" class="mr-1" />
          {{ reviewStatusLabel(document.review_status) }}
          <span v-if="document.last_review_score != null" class="review-pill__score">
            · {{ document.last_review_score.toFixed(0) }} 分
          </span>
        </span>
      </template>
      <template #extra>
        <n-button
          type="primary"
          :loading="reviewing"
          :disabled="reviewing"
          @click="handleReview"
        >
          <template #icon><span class="i-carbon-analytics" /></template>
          {{ reviewing ? "AI 评审中..." : reviews.length > 0 ? "再次发起 AI 评审" : "发起 AI 评审" }}
        </n-button>
      </template>
    </page-header>

    <n-spin :show="loading">
      <template v-if="document">
        <!-- 评审进行中提示条 -->
        <transition name="fade-slide">
          <div v-if="reviewing" class="review-progress">
            <n-progress
              type="line"
              :percentage="reviewProgress"
              :show-indicator="false"
              :height="4"
              :border-radius="0"
              :color="'var(--brand-primary)'"
              :rail-color="'var(--bg-page-soft)'"
              class="review-progress__bar"
            />
            <div class="review-progress__body">
              <span class="i-carbon-machine-learning-model review-progress__icon" />
              <div class="review-progress__text">
                <div class="review-progress__title">{{ reviewStageLabel }}</div>
                <div class="review-progress__hint">
                  AI 正在分析文档结构、完整性、清晰性等维度，请稍候...
                </div>
              </div>
              <n-button size="small" quaternary @click="fetchReviews">
                <template #icon><span class="i-carbon-renew" /></template>
                手动刷新
              </n-button>
            </div>
          </div>
        </transition>

        <!-- 文档基本信息 -->
        <n-card size="small" class="mb-4">
          <template #header>
            <div class="flex items-center gap-2">
              <span class="i-carbon-document text-lg text-blue-500" />
              <span class="font-medium">{{ document.filename }}</span>
              <n-tag
                :type="document.content_type.includes('pdf') || document.filename.endsWith('.pdf') ? 'error' : 'info'"
                size="small"
                :bordered="false"
              >
                {{ docFormatLabel(document) }}
              </n-tag>
            </div>
          </template>

          <n-descriptions :column="3" label-placement="left" bordered size="small">
            <n-descriptions-item label="文件大小">
              {{ formatSize(document.file_size) }}
            </n-descriptions-item>
            <n-descriptions-item label="解析状态">
              <n-tag :type="document.status === 'parsed' ? 'success' : 'warning'" size="small">
                {{ document.status === "parsed" ? "已解析" : document.status }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="评审次数">
              {{ document.review_count }}
            </n-descriptions-item>
            <n-descriptions-item label="上传者">
              {{ document.uploader_name || "-" }}
            </n-descriptions-item>
            <n-descriptions-item label="上传时间">
              {{ formatDate(document.created_at) }}
            </n-descriptions-item>
            <n-descriptions-item label="更新时间">
              {{ formatDate(document.updated_at) }}
            </n-descriptions-item>
          </n-descriptions>
        </n-card>

        <n-card v-if="document.content_text || editingContent" size="small" class="mb-4 doc-content-card">
          <template #header>
            <div class="flex items-center gap-2">
              <span class="i-carbon-text-align-left" />
              <span class="font-medium text-sm">文档内容</span>
              <n-tag size="tiny" :bordered="false" type="info">
                {{ (editingContent ? editingDraft.length : document.content_text?.length || 0) }} 字
              </n-tag>
              <n-tag v-if="editingContent" size="tiny" :bordered="false" type="warning">
                编辑模式
              </n-tag>
            </div>
          </template>
          <template #header-extra>
            <div class="flex items-center gap-2">
              <template v-if="!editingContent">
                <n-button size="tiny" quaternary @click.stop="contentExpanded = !contentExpanded">
                  <template #icon>
                    <span :class="contentExpanded ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" />
                  </template>
                  {{ contentExpanded ? "收起" : "展开全文" }}
                </n-button>
                <n-button size="tiny" quaternary type="primary" @click.stop="enterEdit">
                  <template #icon><span class="i-carbon-edit" /></template>
                  编辑
                </n-button>
              </template>
              <template v-else>
                <n-button size="tiny" quaternary @click.stop="cancelEdit">
                  取消
                </n-button>
                <n-button
                  size="tiny"
                  type="primary"
                  :loading="savingContent"
                  @click.stop="saveContent"
                >
                  <template #icon><span class="i-carbon-save" /></template>
                  保存
                </n-button>
              </template>
            </div>
          </template>
          <n-input
            v-if="editingContent"
            v-model:value="editingDraft"
            type="textarea"
            :autosize="{ minRows: 14, maxRows: 28 }"
            placeholder="可在此处直接修订文档内容（支持 Markdown），保存后再次评审将基于修订后的文本运行。"
            class="doc-edit-textarea"
          />
          <div
            v-else-if="document.content_text"
            class="doc-content"
            :class="{ 'is-collapsed': !contentExpanded }"
            @click="contentExpanded = !contentExpanded"
          >
            <div class="doc-content__text">{{ document.content_text }}</div>
            <div v-if="!contentExpanded && document.content_text.length > 500" class="doc-content__fade">
              <span class="i-carbon-chevron-down mr-1" />
              点击展开全文 ({{ document.content_text.length }} 字)
            </div>
            <div v-else-if="contentExpanded" class="doc-content__collapse-hint">
              <span class="i-carbon-chevron-up mr-1" />
              点击收起
            </div>
          </div>
        </n-card>

        <!-- 评审历史 -->
        <div class="mb-4">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center gap-2">
              <n-text class="text-base font-medium">评审记录</n-text>
              <n-tag v-if="reviews.length > 0" size="tiny" :bordered="false" type="info">
                共 {{ reviews.length }} 次
              </n-tag>
            </div>
            <n-button size="small" quaternary @click="fetchReviews">
              <template #icon><span class="i-carbon-renew" :class="{ 'animate-spin': reviewsLoading }" /></template>
              刷新
            </n-button>
          </div>

          <n-spin :show="reviewsLoading">
            <div v-if="reviews.length > 0" class="space-y-3">
              <div v-for="review in reviews" :key="review.id">
                <review-result
                  v-if="expandedReviewId === review.id && expandedReview"
                  :review="expandedReview"
                />
                <n-card
                  v-else
                  size="small"
                  hoverable
                  class="cursor-pointer review-row"
                  @click="handleExpandReview(review.id)"
                >
                  <div class="flex items-center justify-between">
                    <div class="flex items-center gap-3">
                      <n-tag
                        :type="reviewStatusType(review.status)"
                        size="small"
                        :bordered="false"
                      >
                        {{ reviewStatusLabel(reviewStatusKey(review.status)) }}
                      </n-tag>
                      <span
                        v-if="review.overall_score != null"
                        class="font-bold text-base"
                        :style="{ color: scoreColor(review.overall_score) }"
                      >
                        {{ review.overall_score.toFixed(0) }} 分
                      </span>
                      <n-text v-if="review.summary" depth="3" class="text-sm review-row__summary">
                        {{ review.summary }}
                      </n-text>
                    </div>
                    <div class="flex items-center gap-3 text-xs text-gray-400">
                      <span v-if="review.model_used">{{ review.model_used }}</span>
                      <span>{{ formatDate(review.created_at) }}</span>
                      <n-popconfirm @positive-click="(e: Event | undefined) => handleDeleteReview(review.id, e)">
                        <template #trigger>
                          <n-button
                            size="tiny"
                            quaternary
                            type="error"
                            @click.stop
                          >
                            <template #icon><span class="i-carbon-trash-can" /></template>
                          </n-button>
                        </template>
                        确认删除该评审记录？
                      </n-popconfirm>
                      <span class="i-carbon-chevron-right" />
                    </div>
                  </div>
                </n-card>
              </div>
            </div>
            <n-empty v-else-if="!reviewsLoading && !reviewing" description="暂无评审记录" size="small" class="my-6">
              <template #extra>
                <n-button size="small" type="primary" @click="handleReview">
                  发起第一次 AI 评审
                </n-button>
              </template>
            </n-empty>
          </n-spin>
        </div>
      </template>
    </n-spin>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NInput,
  NPopconfirm,
  NProgress,
  NSpin,
  NTag,
  NText,
  useMessage,
} from "naive-ui";
import PageHeader from "@/components/common/PageHeader.vue";
import {
  deleteReviewApi,
  getDocumentDetailApi,
  getDocumentReviewsApi,
  getReviewDetailApi,
  triggerReviewApi,
  updateDocumentApi,
} from "@/services/requirements";
import type { DocumentDetail, ReviewListItem, ReviewInfo } from "@/services/requirements";
import ReviewResult from "@/components/requirements/ReviewResult.vue";

const route = useRoute();
const router = useRouter();
const message = useMessage();

const documentId = route.params.documentId as string;

const loading = ref(false);
const document = ref<DocumentDetail | null>(null);
const contentExpanded = ref(false);

const reviewing = ref(false);
const reviewProgress = ref(0);
const reviewStage = ref<"submitting" | "analyzing" | "scoring" | "done">("submitting");
let progressTimer: ReturnType<typeof setInterval> | null = null;

const reviewsLoading = ref(false);
const reviews = ref<ReviewListItem[]>([]);
const expandedReviewId = ref<string | null>(null);
const expandedReview = ref<ReviewInfo | null>(null);

const editingContent = ref(false);
const editingDraft = ref("");
const savingContent = ref(false);

function enterEdit() {
  editingDraft.value = document.value?.content_text || "";
  editingContent.value = true;
  contentExpanded.value = true;
}

function cancelEdit() {
  editingContent.value = false;
  editingDraft.value = "";
}

async function saveContent() {
  if (!document.value) return;
  savingContent.value = true;
  try {
    const res = await updateDocumentApi(document.value.id, {
      content_text: editingDraft.value,
    });
    if (res.success) {
      document.value = res.data;
      message.success("文档内容已保存");
      editingContent.value = false;
    }
  } catch {
    message.error("保存失败，请重试");
  } finally {
    savingContent.value = false;
  }
}

async function handleDeleteReview(reviewId: string, evt?: Event) {
  evt?.stopPropagation();
  try {
    const res = await deleteReviewApi(reviewId);
    if (res.success) {
      message.success("评审记录已删除");
      if (expandedReviewId.value === reviewId) {
        expandedReviewId.value = null;
        expandedReview.value = null;
      }
      await Promise.all([fetchReviews(), fetchDocument()]);
    }
  } catch {
    message.error("删除失败");
  }
}

const reviewStageLabel = computed(() => {
  const map: Record<string, string> = {
    submitting: "正在提交评审任务...",
    analyzing: "AI 正在解析文档语义与结构...",
    scoring: "AI 正在维度评分与生成问题清单...",
    done: "评审完成",
  };
  return map[reviewStage.value] || "AI 评审中";
});

function startProgressSimulation() {
  reviewProgress.value = 8;
  reviewStage.value = "submitting";
  progressTimer && clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    if (reviewProgress.value < 35) {
      reviewProgress.value += 3 + Math.random() * 4;
      reviewStage.value = "submitting";
    } else if (reviewProgress.value < 70) {
      reviewProgress.value += 1.5 + Math.random() * 2.5;
      reviewStage.value = "analyzing";
    } else if (reviewProgress.value < 92) {
      reviewProgress.value += 0.4 + Math.random();
      reviewStage.value = "scoring";
    }
    if (reviewProgress.value > 95) reviewProgress.value = 95;
  }, 800);
}

function stopProgressSimulation() {
  if (progressTimer) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
  reviewProgress.value = 100;
  reviewStage.value = "done";
}

async function fetchDocument() {
  loading.value = true;
  try {
    const res = await getDocumentDetailApi(documentId);
    if (res.success) {
      document.value = res.data;
    }
  } catch {
    message.error("获取文档详情失败");
  } finally {
    loading.value = false;
  }
}

async function fetchReviews() {
  reviewsLoading.value = true;
  try {
    const res = await getDocumentReviewsApi(documentId);
    if (res.success) {
      reviews.value = res.data;
    }
  } catch {
    message.error("获取评审记录失败");
  } finally {
    reviewsLoading.value = false;
  }
}

async function handleReview() {
  if (reviewing.value) return;
  reviewing.value = true;
  startProgressSimulation();
  try {
    const res = await triggerReviewApi(documentId);
    // 后端兜底：即使 LLM 失败也会返回 success=true，但 review.status="failed"
    // 把原始错误存进 raw_response。前端必须按 review.status 二次判断，否则
    // 用户会看到"评审完成"绿色 toast 但实际 review 是失败状态。
    if (res.success && res.data?.status === "completed") {
      message.success("AI 评审完成");
      expandedReviewId.value = res.data.id;
      expandedReview.value = res.data;
      await Promise.all([fetchReviews(), fetchDocument()]);
    } else if (res.success && res.data?.status === "failed") {
      const detail = (res.data as { raw_response?: string }).raw_response;
      message.error(
        detail ? `AI 评审失败：${truncate(detail, 200)}` : "AI 评审失败，请查看后端日志",
        { duration: 8000 },
      );
      await Promise.all([fetchReviews(), fetchDocument()]);
    }
  } catch (err) {
    // ofetch 在非 2xx 时会抛 FetchError；把后端 ``message`` / ``code`` 透传出来，
    // 不要再用"评审失败，请重试"这种黑盒字样——422 / 500 用户看到的提示完全
    // 不一样（422 通常是没配 LLM 或文档没解析成功，500 才是真的代码 bug）。
    const data = (err as { data?: { message?: string; code?: string } })?.data;
    const status = (err as { status?: number; statusCode?: number })?.status
      ?? (err as { statusCode?: number })?.statusCode;
    const detail = data?.message
      || (err as Error)?.message
      || "未知错误";
    message.error(`评审失败 (${status ?? "?"}): ${truncate(detail, 200)}`, {
      duration: 8000,
    });
  } finally {
    stopProgressSimulation();
    setTimeout(() => {
      reviewing.value = false;
      reviewProgress.value = 0;
    }, 600);
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}...` : s;
}

async function handleExpandReview(reviewId: string) {
  if (expandedReviewId.value === reviewId) {
    expandedReviewId.value = null;
    expandedReview.value = null;
    return;
  }
  try {
    const res = await getReviewDetailApi(reviewId);
    if (res.success) {
      expandedReviewId.value = reviewId;
      expandedReview.value = res.data;
    }
  } catch {
    message.error("获取评审详情失败");
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString("zh-CN");
}

function docFormatLabel(d: DocumentDetail) {
  const name = d.filename.toLowerCase();
  if (name.endsWith(".pdf") || d.content_type.includes("pdf")) return "PDF";
  if (name.endsWith(".doc")) return "DOC";
  return "DOCX";
}

function reviewStatusLabel(status: string) {
  const map: Record<string, string> = {
    unreviewed: "未评审",
    reviewing: "评审中",
    reviewed: "已评审",
    failed: "评审失败",
  };
  return map[status] || status;
}

function reviewStatusIcon(status: string) {
  const map: Record<string, string> = {
    unreviewed: "i-carbon-circle-dash",
    reviewing: "i-carbon-renew",
    reviewed: "i-carbon-checkmark-outline",
    failed: "i-carbon-warning",
  };
  return map[status] || "i-carbon-circle-dash";
}

function reviewStatusType(s: string): "success" | "warning" | "error" | "info" {
  if (s === "completed") return "success";
  if (s === "failed") return "error";
  return "warning";
}

function reviewStatusKey(s: string) {
  if (s === "completed") return "reviewed";
  if (s === "failed") return "failed";
  return "reviewing";
}

function scoreColor(score: number): string {
  if (score >= 80) return "var(--color-success, #18a058)";
  if (score >= 60) return "var(--color-warning, #f0a020)";
  return "var(--color-error, #d03050)";
}

onMounted(() => {
  fetchDocument();
  fetchReviews();
});

onBeforeUnmount(() => {
  if (progressTimer) clearInterval(progressTimer);
});
</script>

<style scoped>
.review-pill {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 10px;
  font-size: 12px;
  border-radius: 999px;
  font-weight: 500;
  background: var(--bg-page-soft);
  color: var(--text-secondary);
}

.review-pill--reviewed {
  background: rgba(24, 160, 88, 0.12);
  color: var(--color-success, #18a058);
}

.review-pill--reviewing {
  background: rgba(240, 160, 32, 0.12);
  color: var(--color-warning, #f0a020);
}

.review-pill--failed {
  background: rgba(208, 48, 80, 0.12);
  color: var(--color-error, #d03050);
}

.review-pill__score {
  font-weight: 600;
}

.review-progress {
  margin-bottom: 16px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-card);
  border: 1px solid var(--brand-primary-border);
}

.review-progress__body {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
}

.review-progress__icon {
  font-size: 22px;
  color: var(--brand-primary);
  animation: spin-slow 2.4s linear infinite;
}

@keyframes spin-slow {
  to { transform: rotate(360deg); }
}

.review-progress__text {
  flex: 1;
}

.review-progress__title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}

.review-progress__hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

.doc-content-card .doc-content {
  cursor: pointer;
  position: relative;
  border-radius: 8px;
  transition: background-color var(--duration-fast) var(--easing-standard);
}

.doc-content:hover {
  background-color: var(--bg-page-soft);
}

.doc-content__text {
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  color: var(--text-primary);
  padding: 4px 8px;
}

.doc-content.is-collapsed .doc-content__text {
  max-height: 240px;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 70%, transparent 100%);
          mask-image: linear-gradient(180deg, #000 70%, transparent 100%);
}

.doc-content__fade,
.doc-content__collapse-hint {
  text-align: center;
  font-size: 12px;
  color: var(--brand-primary);
  margin-top: 6px;
  padding: 6px;
  background: var(--brand-primary-soft);
  border-radius: 6px;
}

.review-row__summary {
  max-width: 460px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-edit-textarea :deep(.n-input__textarea-el) {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.7;
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s ease;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
