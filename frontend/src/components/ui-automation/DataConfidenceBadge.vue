<template>
  <!-- 数据可信度三态徽章（Task 10.4）。
       业务语义（与后端 ``UICaseResult.data_confidence`` 一一对应）：
         🟢 reliable     — 全部物料按配置使用，结果可信
         🟡 synthesized  — 至少触发了一次 ``platform_synthesize_data``
         🟠 data_failure — 触发了 ``platform_mark_data_failure``，本用例数据环境不可用

       为什么独立成组件而不是直接在父级写 n-tag？
       - 三态在 ExecutionHistory 表格、ExecutionDetail 用例标题、CaseProgress
         live 视图都会用到；独立组件保证语义/视觉/tooltip 100% 一致。
       - 默认带 tooltip 解释三态含义，对新用户友好。 -->
  <n-tooltip
    :disabled="!showTooltip"
    placement="top"
    trigger="hover"
    :show-arrow="true"
  >
    <template #trigger>
      <span
        class="data-confidence-badge"
        :class="[
          `data-confidence-badge--${effectiveValue}`,
          `data-confidence-badge--${size}`,
          variant === 'icon-only' ? 'data-confidence-badge--icon-only' : '',
        ]"
      >
        <span class="data-confidence-badge__dot">{{ meta.emoji }}</span>
        <span v-if="variant !== 'icon-only'" class="data-confidence-badge__label">
          {{ meta.label }}
        </span>
      </span>
    </template>
    <div class="data-confidence-badge__tooltip">
      <div class="data-confidence-badge__tooltip-title">
        {{ meta.emoji }} {{ meta.label }}
      </div>
      <div class="data-confidence-badge__tooltip-desc">
        {{ meta.description }}
      </div>
    </div>
  </n-tooltip>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NTooltip } from "naive-ui";

export type ConfidenceValue = "reliable" | "synthesized" | "data_failure" | null | undefined;

const props = withDefaults(
  defineProps<{
    value: ConfidenceValue;
    /** 尺寸：tiny（表格内）/ small（用例标题）/ medium（详情卡片大徽章） */
    size?: "tiny" | "small" | "medium";
    /** 显示形态：full = emoji + label；icon-only = 仅 emoji 圆点 */
    variant?: "full" | "icon-only";
    /** 是否显示 hover tooltip */
    showTooltip?: boolean;
  }>(),
  {
    size: "small",
    variant: "full",
    showTooltip: true,
  },
);

const META: Record<
  "reliable" | "synthesized" | "data_failure" | "unknown",
  { emoji: string; label: string; description: string }
> = {
  reliable: {
    emoji: "🟢",
    label: "数据可信",
    description:
      "全部物料按配置加载与使用，未触发自造或失败标记；结果可信度高，可作为业务通过率分子。",
  },
  synthesized: {
    emoji: "🟡",
    label: "含 AI 自造",
    description:
      "至少有一项物料是通过 platform_synthesize_data 自造的；建议人工复核后再纳入业务结论。",
  },
  data_failure: {
    emoji: "🟠",
    label: "数据失败",
    description:
      "AI 调用了 platform_mark_data_failure：数据环境不可用，结果不计入业务通过率分母。",
  },
  unknown: {
    emoji: "⚪",
    label: "未知",
    description: "尚未给出可信度评级（用例可能仍在执行中）。",
  },
};

const effectiveValue = computed<"reliable" | "synthesized" | "data_failure" | "unknown">(
  () => {
    if (props.value === "reliable" || props.value === "synthesized" || props.value === "data_failure") {
      return props.value;
    }
    return "unknown";
  },
);

const meta = computed(() => META[effectiveValue.value]);
</script>

<style scoped>
.data-confidence-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 999px;
  font-weight: 600;
  white-space: nowrap;
  border: 1px solid transparent;
  transition: background-color var(--duration-fast) var(--easing-standard);
  cursor: default;
}

/* 三态视觉 ─────────────────────────────────────────────────────────── */

.data-confidence-badge--reliable {
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
  border-color: rgba(22, 163, 74, 0.25);
}

.data-confidence-badge--synthesized {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.3);
}

.data-confidence-badge--data_failure {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.3);
}

.data-confidence-badge--unknown {
  background: var(--bg-page-soft);
  color: var(--text-tertiary);
  border-color: var(--border-subtle);
}

/* 尺寸 ─────────────────────────────────────────────────────────────── */

.data-confidence-badge--tiny {
  font-size: 10px;
  padding: 0 6px;
  min-height: 16px;
  line-height: 16px;
}

.data-confidence-badge--small {
  font-size: 11px;
  padding: 1px 8px;
  min-height: 20px;
  line-height: 18px;
}

.data-confidence-badge--medium {
  font-size: 13px;
  padding: 4px 12px;
  min-height: 26px;
  line-height: 1;
  gap: 6px;
}

/* 仅 emoji 模式（用于密集表格） */

.data-confidence-badge--icon-only {
  padding: 0;
  width: 18px;
  height: 18px;
  justify-content: center;
  border: none;
  background: transparent;
}

.data-confidence-badge--icon-only .data-confidence-badge__dot {
  font-size: 12px;
  line-height: 1;
}

/* tooltip ──────────────────────────────────────────────────────────── */

.data-confidence-badge__tooltip {
  max-width: 260px;
}

.data-confidence-badge__tooltip-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.data-confidence-badge__tooltip-desc {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.85);
  line-height: 1.5;
}
</style>
