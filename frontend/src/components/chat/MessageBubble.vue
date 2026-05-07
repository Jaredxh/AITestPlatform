<template>
  <div
    class="message-bubble"
    :class="{ 'message-bubble--user': isUser }"
  >
    <div class="message-bubble__avatar">
      <n-avatar :size="32" round :class="isUser ? 'avatar--user' : 'avatar--ai'">
        {{ isUser ? "我" : "AI" }}
      </n-avatar>
    </div>

    <div class="message-bubble__main">
      <!-- Phase 12 / Task 12.6 — AI 调用 skill 的徽章（点击展开 SKILL.md） -->
      <skill-usage-badge
        v-if="!isUser && message.skill_invocation_id && skillBadgeId"
        :skill-id="skillBadgeId"
        :reason="skillBadgeReason"
      />

      <div class="message-bubble__content">
        <!-- User message -->
        <div
          v-if="isUser"
          class="whitespace-pre-wrap break-words"
        >{{ displayContent }}</div>

        <!-- Action card: Review result -->
        <template v-else-if="actionType === 'review' && actionMeta?.overall_score != null">
          <div class="action-card">
            <div class="flex items-center gap-2 mb-2">
              <span class="i-carbon-analytics text-blue-500" />
              <span class="font-medium">需求评审结果</span>
              <n-tag v-if="actionMeta.document_name" size="tiny" :bordered="false">
                {{ actionMeta.document_name }}
              </n-tag>
            </div>

            <!-- Score -->
            <div class="flex items-center gap-3 mb-3">
              <n-progress
                type="circle"
                :percentage="actionMeta.overall_score"
                :color="scoreColor(actionMeta.overall_score)"
                :rail-color="'rgba(128,128,128,0.15)'"
                :stroke-width="8"
                :show-indicator="true"
                style="width: 56px;"
              />
              <div>
                <div class="text-lg font-bold" :style="{ color: scoreColor(actionMeta.overall_score) }">
                  {{ actionMeta.overall_score }} 分
                </div>
                <div class="text-xs text-gray-500">综合评分</div>
              </div>
            </div>

            <!-- Summary -->
            <div v-if="actionMeta.summary" class="text-xs text-gray-600 dark:text-gray-300 mb-2">
              {{ actionMeta.summary }}
            </div>

            <!-- Dimensions -->
            <div v-if="actionMeta.dimensions" class="grid grid-cols-5 gap-1 mb-2">
              <div
                v-for="(dim, key) in actionMeta.dimensions"
                :key="key"
                class="text-center p-1 rounded bg-white/50 dark:bg-gray-700/50"
              >
                <div class="text-xs font-medium" :style="{ color: scoreColor(dim.score) }">
                  {{ dim.score }}
                </div>
                <div class="text-[10px] text-gray-400">{{ dimensionLabel(String(key)) }}</div>
              </div>
            </div>

            <!-- Issues count -->
            <div v-if="actionMeta.issues_count" class="text-xs text-gray-400">
              发现 {{ actionMeta.issues_count }} 个问题
            </div>

            <!-- Link -->
            <div class="mt-2 text-xs">
              <a class="text-blue-500 cursor-pointer hover:underline" @click="goToRequirements">
                查看完整评审 →
              </a>
            </div>
          </div>
          <div class="markdown-body mt-2" v-html="renderedHtml" />
        </template>

        <template v-else-if="actionType === 'generate_testcases' && actionMeta?.generated_count">
          <div class="action-card">
            <div class="flex items-center gap-2 mb-2">
              <span class="i-carbon-task text-emerald-500" />
              <span class="font-medium">AI 生成测试用例</span>
              <n-tag v-if="actionMeta.document_name" size="tiny" :bordered="false">
                {{ actionMeta.document_name }}
              </n-tag>
            </div>

            <div class="flex items-center gap-2 mb-2">
              <n-tag type="success" size="small" :bordered="false">
                {{ actionMeta.generated_count }} 条用例
              </n-tag>
              <n-tag v-if="actionMeta.batch_id" size="tiny" :bordered="false">
                批次 {{ actionMeta.batch_id.slice(0, 8) }}
              </n-tag>
            </div>

            <div class="mt-2 text-xs">
              <a class="text-blue-500 cursor-pointer hover:underline" @click="goToTestcases">
                前往用例管理查看 →
              </a>
            </div>
          </div>
          <div class="markdown-body mt-2" v-html="renderedHtml" />
        </template>

        <div v-else-if="!isUser" class="markdown-body" v-html="renderedHtml" />
      </div>

      <div class="message-bubble__meta">
        <n-tag
          v-if="actionType"
          size="tiny"
          :bordered="false"
          :type="actionTagType"
          class="mr-1"
        >
          {{ actionTagLabel }}
        </n-tag>
        <span v-if="message.model_used" class="mr-1">{{ message.model_used }}</span>
        <span>{{ formatTime(message.created_at) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { NAvatar, NProgress, NTag } from "naive-ui";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { ChatMessage } from "@/services/chat";
import type { SkillActivationReason } from "@/services/skills";
import SkillUsageBadge from "@/components/chat/SkillUsageBadge.vue";

const props = defineProps<{
  message: ChatMessage;
}>();

const router = useRouter();

const isUser = computed(() => props.message.role === "user");

const actionMeta = computed(() => props.message.meta_data as Record<string, any> | null);
const actionType = computed(() => actionMeta.value?.action_type as string | undefined);

/**
 * 当前消息消费的 skill 信息：
 * - meta_data.skill_id / skill_name 由后台异步事件回写（agent_callable lazy load 时
 *   服务端会把 skill_id 顺便塞入 meta_data，前端兜底用）；
 * - 没有 cached 时仍能显示徽章（只是 modal 打开后再去拉详情）。
 */
const skillBadgeId = computed<string | null>(() => {
  const meta = actionMeta.value || {};
  const fromMeta = (meta.skill_id as string | undefined) || null;
  // skill_invocation_id 是 SkillUsageLog.id 而非 Skill.id；当 meta 里没有 skill_id 时，
  // 我们仍以 skill_invocation_id 触发徽章渲染，但必须有 skill_id 才能 fetch 详情。
  // 没有 skill_id 时退化为不可点击的纯文本徽章会更稳——这里直接用 fromMeta。
  return fromMeta;
});

const skillBadgeReason = computed<SkillActivationReason>(() => {
  const reason = (actionMeta.value || {}).skill_activation_reason as
    | SkillActivationReason
    | undefined;
  return reason || "agent_callable";
});

const actionTagLabel = computed(() => {
  switch (actionType.value) {
    case "review":
      return "评审";
    case "generate_testcases":
      return "生成用例";
    default:
      return "";
  }
});

const actionTagType = computed<"info" | "success" | "default">(() => {
  switch (actionType.value) {
    case "review":
      return "info";
    case "generate_testcases":
      return "success";
    default:
      return "default";
  }
});

const displayContent = computed(() => {
  const c = props.message.content;
  if (c.includes("[附件:") || c.includes("[图片:")) {
    const parts = c.split(/\n\n(?=[^\[])/).filter(Boolean);
    return parts[parts.length - 1] || c;
  }
  return c;
});

const renderedHtml = computed(() => {
  if (isUser.value) return "";
  const html = marked.parse(props.message.content, {
    async: false,
    breaks: true,
    gfm: true,
  }) as string;
  return DOMPurify.sanitize(html);
});

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "#999";
  if (score >= 80) return "#18a058";
  if (score >= 60) return "#f0a020";
  return "#d03050";
}

const dimensionLabels: Record<string, string> = {
  completeness: "完整性",
  clarity: "清晰性",
  consistency: "一致性",
  testability: "可测试性",
  feasibility: "可行性",
};

function dimensionLabel(key: string): string {
  return dimensionLabels[key] || key;
}

function goToRequirements() {
  router.push({ name: "RequirementList" });
}

function goToTestcases() {
  router.push({ name: "TestcaseList" });
}

function formatTime(dateStr: string) {
  const d = new Date(dateStr);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
</script>

<style scoped>
.message-bubble {
  display: flex;
  gap: 12px;
  padding: 8px 24px;
}

.message-bubble--user {
  flex-direction: row-reverse;
}

.message-bubble__avatar {
  flex-shrink: 0;
  margin-top: 2px;
}

:deep(.avatar--user) {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
  color: #fff !important;
  font-weight: 600;
}

:deep(.avatar--ai) {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
  color: #fff !important;
  font-weight: 600;
}

.message-bubble__main {
  min-width: 0;
  max-width: 75%;
  display: flex;
  flex-direction: column;
}

.message-bubble--user .message-bubble__main {
  align-items: flex-end;
}

.message-bubble__content {
  border-radius: 14px 14px 14px 4px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.65;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  word-break: break-word;
}

.message-bubble--user .message-bubble__content {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  color: #fff;
  border-color: transparent;
  border-radius: 14px 14px 4px 14px;
}

.message-bubble--user .message-bubble__content :deep(*) {
  color: #fff;
}

.message-bubble__meta {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.action-card {
  padding: 4px 0 8px;
}
</style>
