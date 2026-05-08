/**
 * Phase 13 / Task 13.3 — ConfirmationCard "确认执行"派发 + 原地变身。
 *
 * 流程：
 *   1. ConfirmationCard.handleConfirm() 调 confirmExecutionPlanApi(plan_id)
 *   2. 后端 200 返回 task_id（200~500ms）
 *   3. 前端把当前 message_id 对应的 ChatMessage 原地从 kind=skill_card 变成
 *      kind=task_badge，meta_data 替换为 TaskBadgeMeta（task_id + plan 摘要）
 *   4. 输入框立刻可用；TaskBadge 自身后续走 system-events SSE 增量刷进度
 *
 * 设计要点：**同 message_id**——不 append 新消息，避免在历史里积累两条。
 * 这样不论用户是否在线，刷新后从 GET messages 拉到的就是已变身的 task_badge
 * 消息；后续完成事件作为另一条 kind=execution_event 在末尾追加。
 */

import type { ExecutionPlanCard, TaskBadgeMeta } from "@/components/skills/types";
import type { ChatMessage } from "@/services/chat";

export interface ExecutionPlanConfirmResult {
  taskId: string;
  plan: ExecutionPlanCard;
  messageId: string;
}

/**
 * 把 ConfirmationCard 消息变身为 TaskBadge 消息（不改 message_id）。
 *
 * 调用方传入"当前会话所有消息"+"目标 message_id"+ 派发结果，本函数返回
 * **新的消息列表**——上层 useChat 用结果替换 messagesBySession，并触发响应式
 * 渲染。后端那条消息的 kind / meta_data 由后台执行回调 / 重连后 GET messages
 * 自动同步，前端不必显式 PATCH。
 */
export function transformPlanMessageToTaskBadge(
  messages: ChatMessage[],
  result: ExecutionPlanConfirmResult,
): ChatMessage[] {
  return messages.map((m) => {
    if (m.id !== result.messageId) return m;
    const env = result.plan.environment;
    const cases = result.plan.cases;
    const title =
      cases.length > 1
        ? `${cases[0]?.title || "用例"} 等 ${cases.length} 条`
        : cases[0]?.title || "UI 自动化任务";
    const meta: TaskBadgeMeta = {
      task_id: result.taskId,
      status: "pending",
      total_cases: cases.length,
      passed_cases: 0,
      failed_cases: 0,
      skipped_cases: 0,
      duration_ms: null,
      plan_id: result.plan.plan_id,
      title,
      environment_name: env.name,
    };
    return {
      ...m,
      kind: "task_badge",
      content: `已派发执行：${title}（环境：${env.name}）`,
      meta_data: { action_type: "task_badge", ...meta },
    };
  });
}

/**
 * 把 task_status / execution_event SSE 事件中的字段合并到 TaskBadge meta。
 * 仅更新数值字段，不改 task_id / plan_id 等不可变字段。
 */
export function mergeTaskBadgeMeta(
  current: TaskBadgeMeta,
  patch: Partial<TaskBadgeMeta>,
): TaskBadgeMeta {
  return { ...current, ...patch };
}

export function applyTaskBadgePatch(
  messages: ChatMessage[],
  taskId: string,
  patch: Partial<TaskBadgeMeta>,
): ChatMessage[] {
  return messages.map((m) => {
    if (m.kind !== "task_badge") return m;
    const meta = (m.meta_data || {}) as Record<string, unknown> & TaskBadgeMeta;
    if (meta.task_id !== taskId) return m;
    const merged = mergeTaskBadgeMeta(meta, patch);
    return {
      ...m,
      meta_data: { ...meta, ...merged },
    };
  });
}
