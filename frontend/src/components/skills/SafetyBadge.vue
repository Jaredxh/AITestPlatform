<template>
  <n-tag :type="badgeType" size="small" :bordered="false" :round="round">
    <template #icon>
      <span :class="iconClass" />
    </template>
    {{ label }}
  </n-tag>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NTag } from "naive-ui";
import {
  SKILL_SAFETY_BADGE_TYPE,
  SKILL_SAFETY_LABEL,
  type SkillSafetyStatus,
} from "@/services/skills";

const props = defineProps<{
  status: SkillSafetyStatus;
  round?: boolean;
}>();

const badgeType = computed(() => SKILL_SAFETY_BADGE_TYPE[props.status] ?? "default");
const label = computed(() => SKILL_SAFETY_LABEL[props.status] ?? props.status);
const iconClass = computed(() => {
  switch (props.status) {
    case "clean":
      return "i-carbon-checkmark-filled";
    case "warning":
      return "i-carbon-warning-alt";
    case "blocked":
      return "i-carbon-warning";
    default:
      return "i-carbon-help";
  }
});
</script>
