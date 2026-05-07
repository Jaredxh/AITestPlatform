/**
 * 管理 chat 中"用户手动选中的技能"列表（Phase 12 / Task 12.6）。
 *
 * 数据流：
 *   - `loadAvailable(projectId)` 从 ``/projects/:id/skills?is_enabled=true`` 拉
 *     当前项目所有可选 skill，作为下拉选项的源；
 *   - `setSelected(sessionId, ids)` 写入后端
 *     ``/projects/:id/skills/chat/activate-manual``（持久化到
 *     ``chat_sessions.chat_context.manual_skill_ids``）；
 *   - 切会话 / 刷新页面时，调用 `syncFromSession(session)` 把 session 上已有的
 *     manual_skill_ids 反灌给 UI。
 *
 * 不在此处订阅 SSE / 调 chat header —— 这些由调用方注入；本 composable 只持有
 * 选中状态与远端写入逻辑。
 */

import { ref, computed } from "vue";
import {
  activateManualSkillsApi,
  listSkillsApi,
  type SkillListItem,
} from "@/services/skills";
import type { ChatSession } from "@/services/chat";

export function useSkillSelection() {
  const available = ref<SkillListItem[]>([]);
  const selectedIds = ref<string[]>([]);
  const isLoading = ref(false);
  const isPersisting = ref(false);

  const selectedCount = computed(() => selectedIds.value.length);

  async function loadAvailable(projectId: string | null) {
    if (!projectId) {
      available.value = [];
      return;
    }
    isLoading.value = true;
    try {
      const res = await listSkillsApi(projectId, {
        page: 1,
        page_size: 200,
        is_enabled: true,
      });
      if (res.success) {
        available.value = res.data.items;
      }
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * 把 session.chat_context.manual_skill_ids 同步到 UI。后端 ChatSession schema
   * 暂未把 chat_context 暴露到响应里——这里同时支持显式传 ids 兜底。
   */
  function syncFromSession(_session: ChatSession | null, ids?: string[]) {
    if (Array.isArray(ids)) {
      selectedIds.value = [...ids];
      return;
    }
    selectedIds.value = [];
  }

  async function setSelected(
    projectId: string,
    sessionId: string,
    ids: string[],
  ) {
    selectedIds.value = [...ids];
    if (!projectId || !sessionId) return;
    isPersisting.value = true;
    try {
      const res = await activateManualSkillsApi(projectId, sessionId, ids);
      if (res.success) {
        selectedIds.value = res.data.manual_skill_ids;
      }
    } finally {
      isPersisting.value = false;
    }
  }

  function clear() {
    selectedIds.value = [];
    available.value = [];
  }

  return {
    available,
    selectedIds,
    selectedCount,
    isLoading,
    isPersisting,
    loadAvailable,
    syncFromSession,
    setSelected,
    clear,
  };
}
