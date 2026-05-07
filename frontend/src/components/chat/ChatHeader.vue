<template>
  <header class="chat-header">
    <!-- 第一行：会话标题独占，避免与状态标签 / 选项卡挤在一行 -->
    <div class="chat-header__row chat-header__row--title">
      <span class="i-carbon-chat-bot chat-header__icon" />
      <span class="chat-header__title" :title="session?.title || 'AI 对话'">
        {{ session?.title || "AI 对话" }}
      </span>
    </div>

    <!-- 第二行：状态标签 + 操作选项卡 -->
    <div class="chat-header__row chat-header__row--meta">
      <div class="chat-header__tags">
        <n-tag
          v-if="currentModelLabel"
          size="small"
          :bordered="false"
          type="info"
          class="chat-header__tag"
        >
          <template #icon><span class="i-carbon-machine-learning-model" /></template>
          <span class="chat-header__tag-text">{{ currentModelLabel }}</span>
        </n-tag>
        <n-tag size="small" :bordered="false" type="success" class="chat-header__tag">
          <template #icon><span class="i-carbon-bot" /></template>
          Agent
        </n-tag>
      </div>

      <div class="chat-header__actions">
        <n-tooltip v-if="canManageSkills" placement="bottom">
          <template #trigger>
            <n-button
              size="small"
              quaternary
              circle
              class="chat-header__icon-only"
              @click="goToSkillManagement"
            >
              <template #icon>
                <span class="i-carbon-settings-adjust" />
              </template>
            </n-button>
          </template>
          管理技能包
        </n-tooltip>

        <n-select
          v-if="promptOptions.length > 0"
          :value="selectedPromptId"
          :options="promptOptions"
          size="small"
          placeholder="提示词"
          clearable
          class="chat-header__select chat-header__select--prompt"
          @update:value="handlePromptChange"
        />
        <n-select
          v-if="configs.length > 0"
          :value="selectedConfigId"
          :options="configOptions"
          size="small"
          placeholder="模型"
          class="chat-header__select chat-header__select--model"
          @update:value="$emit('update:selectedConfigId', $event)"
        />
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { NSelect, NTag, NButton, NTooltip } from "naive-ui";
import type { ChatSession } from "@/services/chat";
import type { LLMConfigInfo } from "@/services/llm";
import type { PromptListItem } from "@/services/prompts";
import { usePermission } from "@/composables/usePermission";

// 技能选择改回"完全自动"模式：触发词命中、always、agent_callable 由后端
// SkillRouter.compose 决定。这里不再暴露"手动多选 skill"按钮——它对普通用户
// 没有可发现性、徒增 header 拥挤；后端 ``/skills/chat/activate-manual``
// 接口仍保留，供 power-user / 命令行场景，未来如需回归再接回。
const props = defineProps<{
  session: ChatSession | null;
  configs: LLMConfigInfo[];
  selectedConfigId: string | null;
  prompts: PromptListItem[];
  selectedPromptId: string | null;
}>();

const emit = defineEmits<{
  "update:selectedConfigId": [value: string];
  "update:selectedPromptId": [value: string | null];
}>();

const router = useRouter();
const { has: hasPerm } = usePermission();
const canManageSkills = computed(() => hasPerm("skill:view"));

const configOptions = computed(() =>
  props.configs.map((c) => ({
    label: `${c.name}（${c.model}）${c.is_default ? " · 默认" : ""}`,
    value: c.id,
  })),
);

const currentModelLabel = computed(() => {
  if (!props.selectedConfigId) {
    return props.session?.llm_config_name || "";
  }
  const cfg = props.configs.find((c) => c.id === props.selectedConfigId);
  if (!cfg) return props.session?.llm_config_name || "";
  return `${cfg.name} · ${cfg.model}`;
});

const promptOptions = computed(() =>
  props.prompts.map((p) => ({
    label: `${p.is_default ? "⭐ " : ""}${p.name}`,
    value: p.id,
  })),
);

function handlePromptChange(value: string | null) {
  emit("update:selectedPromptId", value);
}

function goToSkillManagement() {
  router.push({ name: "SkillManagement" });
}
</script>

<style scoped>
.chat-header {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-card);
  box-sizing: border-box;
}

.chat-header__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 第一行：标题独占 — 不与下方标签/选项卡挤一行；长标题用省略号 */
.chat-header__row--title {
  min-height: 24px;
}

.chat-header__icon {
  color: var(--brand-primary);
  font-size: 18px;
  flex-shrink: 0;
}

.chat-header__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 第二行：tags 居左、actions 居右。
 *
 * 历史 bug：``flex-wrap: wrap`` + tags 文本可变（model tag 显示
 * "{name} · {model}" 切换模型时长度跳变）会让 ``actions`` 在某次切换中
 * 突然折到第二行，第二行只剩两个 select 视觉错位。
 *
 * 修复：整行 ``nowrap`` —— 永远不换行；让 tags 区主动收缩（其内部 tag
 * 文本本来就支持 ellipsis），actions 区保留固定尺寸不被挤出。 */
.chat-header__row--meta {
  flex-wrap: nowrap;
  justify-content: space-between;
}

/* tags 区"被收缩"才是预期：``flex: 1 1 auto`` 占满剩余，``min-width: 0``
 * 允许其内部 tag 文本一旦超长就走 ellipsis；不写 ``flex-wrap: wrap``，避免
 * 多个 tag 自身又触发竖向堆叠。 */
.chat-header__tags {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.chat-header__tag {
  max-width: 100%;
  /* 关键：让 model tag 在容器收缩时先于 Agent tag 让步 */
  min-width: 0;
}

.chat-header__tag-text {
  display: inline-block;
  /* 切换长名模型时不再撑出整行——上限收紧到 240px，超出走 ellipsis；
   * 实际可读区域仍足够展示 ``deepseek-chat / gpt-4o-mini`` 这类常见模型名。 */
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

/* actions 区固定尺寸：``flex-shrink: 0`` 永远不被 tags 挤压；``nowrap``
 * 保证 manage-skill 按钮 + prompt select + model select 始终一行。 */
.chat-header__actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-shrink: 0;
  margin-left: auto;
}

.chat-header__icon-only {
  flex-shrink: 0;
}

.chat-header__select {
  width: 200px;
  flex-shrink: 0;
}

.chat-header__select--model {
  width: 220px;
}

/* 真窄屏（< 720px）才回退到换行布局——日常 1280+ 桌面环境永不触发，
 * 切换模型时不会再"突然换行"。 */
@media (max-width: 720px) {
  .chat-header__row--meta {
    flex-wrap: wrap;
    row-gap: 8px;
  }
  .chat-header__actions {
    flex-wrap: wrap;
  }
  .chat-header__select,
  .chat-header__select--model {
    width: min(200px, 100%);
  }
}
</style>
