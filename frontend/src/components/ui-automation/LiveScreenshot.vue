<template>
  <!-- 步骤实时截图（Task 10.2）。
       后端 step_complete 事件在持久化路径存在时会带 screenshot_url
       (如 /api/ui-executions/steps/{step_id}/screenshot)。当前 Engine
       还没在实时路径捕获 step screenshot，所以多数情况下没值；这里要兜
       两种状态：有图 → 缩略图 + 点击放大；无图 → 等待占位。 -->
  <div class="live-shot" :class="{ 'live-shot--empty': !url }">
    <template v-if="url">
      <n-image
        :src="url"
        :preview-src="url"
        :width="thumbWidth"
        :height="thumbHeight"
        :preview-disabled="!enableZoom"
        object-fit="cover"
        class="live-shot__img"
      >
        <template #error>
          <div class="live-shot__error">
            <span class="i-carbon-image-error" />
            <span>截图加载失败</span>
          </div>
        </template>
      </n-image>
      <span v-if="caption" class="live-shot__caption">{{ caption }}</span>
    </template>
    <template v-else>
      <div class="live-shot__placeholder" :style="placeholderStyle">
        <span class="i-carbon-camera" />
        <span class="text-xs">{{ emptyText }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { NImage } from "naive-ui";

const props = withDefaults(
  defineProps<{
    url?: string | null;
    caption?: string;
    /** 缩略图宽度（px） */
    thumbWidth?: number;
    /** 缩略图高度（px） */
    thumbHeight?: number;
    /** 点击是否触发 naive-ui 自带的放大查看 */
    enableZoom?: boolean;
    /** 无图时占位文本 */
    emptyText?: string;
  }>(),
  {
    url: null,
    caption: undefined,
    thumbWidth: 160,
    thumbHeight: 100,
    enableZoom: true,
    emptyText: "等待截图…",
  },
);

const placeholderStyle = computed(() => ({
  width: `${props.thumbWidth}px`,
  height: `${props.thumbHeight}px`,
}));
</script>

<style scoped>
.live-shot {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.live-shot__img {
  border-radius: var(--radius-sm);
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  cursor: zoom-in;
  background: var(--bg-page-soft);
}

.live-shot__caption {
  font-size: 11px;
  color: var(--text-tertiary);
}

.live-shot__placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  background: var(--bg-page-soft);
  font-size: 12px;
}

.live-shot__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 100%;
  height: 100%;
  color: var(--color-error);
  font-size: 12px;
}
</style>
