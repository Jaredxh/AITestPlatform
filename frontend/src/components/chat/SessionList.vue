<template>
  <div class="session-list">
    <div class="session-list__head">
      <n-button block type="primary" @click="$emit('create')">
        <template #icon><span class="i-carbon-add" /></template>
        新建对话
      </n-button>
    </div>

    <div class="session-list__body">
      <n-spin :show="loading" class="min-h-20">
        <div v-if="sessions.length === 0 && !loading" class="session-list__empty">
          <span class="i-carbon-chat text-2xl mb-2 opacity-60" />
          <span>暂无对话</span>
        </div>
        <div
          v-for="session in sessions"
          :key="session.id"
          class="session-item group"
          :class="{ 'is-active': session.id === activeId }"
          @click="$emit('select', session.id)"
        >
          <div class="session-item__inner">
            <div class="session-item__content">
              <div class="session-item__title">
                {{ session.title || "新对话" }}
              </div>
              <div class="session-item__meta">
                <span v-if="session.llm_config_name" class="session-item__model">
                  <span class="i-carbon-machine-learning-model" />
                  {{ session.llm_config_name }}
                </span>
                <span>{{ formatTime(session.updated_at) }}</span>
              </div>
            </div>
            <n-popconfirm
              placement="right"
              @positive-click="$emit('delete', session.id)"
            >
              <template #trigger>
                <n-button
                  quaternary
                  circle
                  size="tiny"
                  class="session-item__delete"
                  @click.stop
                >
                  <template #icon><span class="i-carbon-trash-can text-xs" /></template>
                </n-button>
              </template>
              确认删除此对话？
            </n-popconfirm>
          </div>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { NButton, NPopconfirm, NSpin } from "naive-ui";
import type { ChatSession } from "@/services/chat";

defineProps<{
  sessions: ChatSession[];
  activeId: string | null;
  loading: boolean;
}>();

defineEmits<{
  select: [id: string];
  create: [];
  delete: [id: string];
}>();

function formatTime(dateStr: string) {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }
  return d.toLocaleDateString("zh-CN");
}
</script>

<style scoped>
.session-list {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.session-list__head {
  padding: 14px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.session-list__body {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px 12px;
}

.session-list__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 12px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.session-item {
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--easing-standard);
  margin-bottom: 2px;
}

.session-item:hover {
  background-color: var(--bg-hover);
}

.session-item.is-active {
  background-color: var(--brand-primary-soft);
}

.session-item.is-active .session-item__title {
  color: var(--brand-primary);
}

.session-item__inner {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 4px;
  padding: 9px 12px;
}

.session-item__content {
  flex: 1;
  min-width: 0;
}

.session-item__title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-item__meta {
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.session-item__model {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item__delete {
  opacity: 0 !important;
  flex-shrink: 0;
  transition: opacity var(--duration-fast) var(--easing-standard);
}

.session-item:hover .session-item__delete,
.session-item.is-active .session-item__delete {
  opacity: 1 !important;
}
</style>
