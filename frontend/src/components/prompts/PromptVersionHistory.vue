<template>
  <n-drawer v-model:show="visible" :width="520" placement="right">
    <n-drawer-content title="版本历史" closable>
      <n-spin :show="loading">
        <n-timeline v-if="versions.length > 0">
          <n-timeline-item
            v-for="v in versions"
            :key="v.id"
            :type="v.version === currentVersion ? 'success' : 'default'"
            :title="`v${v.version}${v.version === currentVersion ? ' (当前)' : ''}`"
            :time="formatDate(v.created_at)"
          >
            <template #header>
              <div class="flex items-center gap-2">
                <span class="font-medium">v{{ v.version }}</span>
                <n-tag v-if="v.version === currentVersion" size="tiny" type="success">当前</n-tag>
                <span class="text-xs text-gray-400">{{ v.creator_name || "未知" }}</span>
              </div>
            </template>
            <div v-if="v.change_note" class="text-sm text-gray-500 mb-2">
              {{ v.change_note }}
            </div>
            <n-collapse>
              <n-collapse-item title="查看内容" name="content">
                <n-code :code="v.content" language="markdown" word-wrap class="text-xs" />
              </n-collapse-item>
            </n-collapse>
          </n-timeline-item>
        </n-timeline>
        <n-empty v-else description="暂无版本历史" />
      </n-spin>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import {
  NDrawer,
  NDrawerContent,
  NTimeline,
  NTimelineItem,
  NTag,
  NCode,
  NCollapse,
  NCollapseItem,
  NSpin,
  NEmpty,
} from "naive-ui";
import { getPromptVersionsApi } from "@/services/prompts";
import type { PromptVersion } from "@/services/prompts";

const props = defineProps<{
  promptId: string | null;
  currentVersion: number;
}>();

const visible = defineModel<boolean>("show", { default: false });

const versions = ref<PromptVersion[]>([]);
const loading = ref(false);

watch(
  () => [visible.value, props.promptId],
  async ([show, id]) => {
    if (show && id) {
      loading.value = true;
      try {
        const res = await getPromptVersionsApi(id as string);
        if (res.success) versions.value = res.data;
      } finally {
        loading.value = false;
      }
    }
  },
);

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleString("zh-CN");
}
</script>
