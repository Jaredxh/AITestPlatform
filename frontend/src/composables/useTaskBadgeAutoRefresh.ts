/**
 * Phase 13 / Task 13.3 — TaskBadge 离线/重连刷新（保底机制）。
 *
 * 一般实时刷新走 `system-events` SSE（task_status 事件），但以下场景拿不到推送：
 *   - 用户切到别的会话再切回（旧 SSE 已断）
 *   - 浏览器长时间放后台后回来（网络层断了 SSE）
 *   - 多 worker 部署，原 worker 拿不到新 worker 完成事件
 *
 * 故在 TaskBadge 组件挂载时主动调一次 `GET /api/ui-executions/{id}` 把状态
 * 拉到最新（见 ``TaskBadge.vue:onMounted``）。本 composable 提供"按需批量
 * 刷新"接口，给"切回会话时一次性把所有非终态 TaskBadge 的状态都拉一遍"的
 * 上层调度使用——避免逐个组件并发打太多请求。
 */

import { getExecutionApi } from "@/services/uiAutomation";
import type { ChatMessage } from "@/services/chat";
import type { TaskBadgeMeta } from "@/components/skills/types";
import { applyTaskBadgePatch } from "./useExecutionPlan";

const TERMINAL = new Set(["completed", "stopped", "failed", "aborted_budget"]);

/**
 * 扫一遍消息列表里所有非终态的 TaskBadge，并发拉最新态后返回新列表。
 * 失败的单条不影响其它（ promiseSettled），调用方拿到结果后应替换 messages。
 */
export async function refreshAllTaskBadges(
  messages: ChatMessage[],
): Promise<ChatMessage[]> {
  const taskIds: string[] = [];
  for (const m of messages) {
    if (m.kind !== "task_badge") continue;
    const meta = (m.meta_data || {}) as Partial<TaskBadgeMeta>;
    if (!meta.task_id) continue;
    if (meta.status && TERMINAL.has(meta.status.toLowerCase())) continue;
    taskIds.push(meta.task_id);
  }
  if (taskIds.length === 0) return messages;

  const settled = await Promise.allSettled(
    taskIds.map((tid) => getExecutionApi(tid)),
  );

  let next = messages;
  settled.forEach((res, idx) => {
    const tid = taskIds[idx];
    if (res.status !== "fulfilled") return;
    const data = res.value?.data;
    if (!data) return;
    next = applyTaskBadgePatch(next, tid, {
      status: data.status,
      total_cases: data.total_cases,
      passed_cases: data.passed_cases,
      failed_cases: data.failed_cases,
      skipped_cases: data.skipped_cases,
      duration_ms: data.duration_ms,
    });
  });
  return next;
}
