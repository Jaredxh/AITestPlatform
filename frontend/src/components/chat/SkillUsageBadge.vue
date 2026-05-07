<template>
  <div v-if="skillName" class="skill-usage-badge">
    <button
      class="skill-usage-badge__btn"
      :class="`skill-usage-badge__btn--${reason}`"
      type="button"
      @click="openModal"
    >
      <span class="i-carbon-skill-level skill-usage-badge__icon" />
      <span class="skill-usage-badge__name">{{ skillName }}</span>
      <n-tag
        size="tiny"
        :bordered="false"
        :type="reasonTagType"
        class="skill-usage-badge__reason"
      >
        {{ reasonLabel }}
      </n-tag>
    </button>

    <n-modal
      v-model:show="modalShow"
      preset="card"
      style="width: 720px; max-width: 90vw"
      :bordered="false"
      :title="modalTitle"
      size="small"
    >
      <div v-if="loading" class="skill-usage-badge__loading">
        <n-spin size="small" />
        <span>加载技能详情…</span>
      </div>
      <template v-else-if="detail">
        <div class="skill-usage-badge__meta">
          <n-tag size="small" :bordered="false" type="info">
            {{ detail.slug }}
          </n-tag>
          <n-tag size="small" :bordered="false">
            v{{ detail.semantic_version }} · db {{ detail.db_version }}
          </n-tag>
          <n-tag
            size="small"
            :bordered="false"
            :type="reasonTagType"
          >
            {{ reasonLabel }}
          </n-tag>
        </div>
        <p class="skill-usage-badge__desc">{{ detail.description }}</p>
        <div class="skill-usage-badge__body markdown-body" v-html="renderedBody" />
      </template>
      <n-empty v-else description="技能详情已被清理" />
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag, NModal, NSpin, NEmpty } from "naive-ui";
import { marked } from "marked";
import DOMPurify from "dompurify";
import {
  getSkillApi,
  type SkillDetail,
  type SkillActivationReason,
  SKILL_ACTIVATION_REASON_LABEL,
} from "@/services/skills";

interface Props {
  skillId: string;
  /** 已知则避免一次预取（来自 SkillActivationHint 缓存或父组件）。 */
  cachedName?: string | null;
  /** 显示徽章颜色用：trigger_match / agent_callable / manual / always */
  reason?: SkillActivationReason;
}

const props = withDefaults(defineProps<Props>(), {
  cachedName: null,
  reason: "agent_callable",
});

const detail = ref<SkillDetail | null>(null);
const loading = ref(false);
const modalShow = ref(false);

const skillName = computed(() => detail.value?.name || props.cachedName || "技能");

const reasonLabel = computed(
  () => SKILL_ACTIVATION_REASON_LABEL[props.reason] || props.reason,
);

const reasonTagType = computed<"info" | "success" | "warning">(() => {
  if (props.reason === "trigger_match") return "success";
  if (props.reason === "always") return "warning";
  return "info";
});

const modalTitle = computed(() => `🎯 ${skillName.value}`);

const renderedBody = computed(() => {
  if (!detail.value?.body) return "";
  const html = marked.parse(detail.value.body, {
    async: false,
    breaks: true,
    gfm: true,
  }) as string;
  return DOMPurify.sanitize(html);
});

async function ensureDetail() {
  if (detail.value) return;
  loading.value = true;
  try {
    const res = await getSkillApi(props.skillId);
    if (res.success) {
      detail.value = res.data;
    }
  } finally {
    loading.value = false;
  }
}

async function openModal() {
  modalShow.value = true;
  await ensureDetail();
}
</script>

<style scoped>
.skill-usage-badge {
  margin-bottom: 4px;
}

.skill-usage-badge__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: rgba(99, 102, 241, 0.1);
  color: var(--brand-primary);
  font-size: 12px;
  cursor: pointer;
  transition: background-color 160ms ease;
}

.skill-usage-badge__btn:hover {
  background: rgba(99, 102, 241, 0.18);
}

.skill-usage-badge__btn--trigger_match {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}

.skill-usage-badge__btn--always {
  background: rgba(245, 158, 11, 0.14);
  color: #d97706;
}

.skill-usage-badge__btn--manual {
  background: rgba(139, 92, 246, 0.14);
  color: #7c3aed;
}

.skill-usage-badge__icon {
  font-size: 14px;
}

.skill-usage-badge__name {
  font-weight: 500;
}

.skill-usage-badge__reason {
  margin-left: 2px;
}

.skill-usage-badge__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 4px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.skill-usage-badge__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.skill-usage-badge__desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.55;
  margin: 4px 0 12px;
}

.skill-usage-badge__body {
  border-top: 1px dashed var(--border-subtle);
  padding-top: 12px;
  font-size: 13.5px;
}

.skill-usage-badge__body :deep(h1),
.skill-usage-badge__body :deep(h2),
.skill-usage-badge__body :deep(h3) {
  margin-top: 12px;
  margin-bottom: 6px;
}

.skill-usage-badge__body :deep(code) {
  background: var(--bg-active);
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 0.9em;
}

.skill-usage-badge__body :deep(pre) {
  background: var(--bg-page);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
}
</style>
