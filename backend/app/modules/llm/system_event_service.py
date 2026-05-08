"""异步执行结果回流总线（Phase 13 / Task 13.0）。

本模块解决"用户在 chat 派发了一次 UI 自动化执行 → 后台跑 5-10 分钟 → 完成
时如何把结果送回到原会话"的核心问题。设计依据：``docs/PHASE3_DESIGN.md
§10.8 / §10.10``。

> 设计文档把模块名定为 ``app/modules/chat/system_event_service.py``，但项目实
> 际把 chat session/message 与 LLM config 同放在 ``app/modules/llm/``；为减少
> 跨模块拆迁工作量与避免 import 走环，本 task 阶段把模块落在 ``llm/`` 下，
> 后续如需按子域重组再统一搬迁。

两条独立机制：

1. **落库即送达** —— ``publish_execution_done`` 写一条 ``ChatMessage(kind=
   'execution_event')`` 到目标 session；用户重连 / 刷新 / 后续 GET messages
   都能拉到这条消息，**永远不丢**。这是设计文档 §10.10 给出的"用户长时间
   离线"兜底策略，不依赖任何长连接。
2. **进程内 SSE 总线** —— ``SYSTEM_EVENT_BUS`` 按 ``session_id`` pub/sub；
   在线的前端 chat 视图（task 13.3 会接的 SSE endpoint）订阅此总线即可在用户
   不刷新的情况下把消息原地刷到末尾。本 task 不连前端 endpoint，仅提供总线
   骨架以便后续 task 接入；落库 + 总线是正交的两条路径，断哪条都不影响
   另一条。

不主动改 ``_handle_chat_stream``：当前会话**正在流式**期间收到系统事件时，
执行回流仍只走"落库 + 总线"——前端 task 13.3 一旦订阅总线，会在不打断当前
chat 流的前提下"末尾追加"系统消息（设计文档 §10.8 的体验保证 1/2/3）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.models import ChatMessage, ChatSession

logger = logging.getLogger(__name__)


# ─────────────────── 进程内 SSE 总线 ────────────────────────────────


@dataclass
class _SessionChannel:
    """单 session 的事件队列广播器；多个订阅者各自维护独立 ``asyncio.Queue``。"""

    queues: list[asyncio.Queue] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self.queues)
        # 不在锁内 put：避免某个慢消费者卡住其它订阅者
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 兜底：消费者明显跟不上时丢弃，避免内存堆积；这条事件仍已在
                # DB 持久化，用户重连后从 GET messages 拉到。
                logger.warning("system event queue full; dropping event for one subscriber")

    async def subscribe(self) -> tuple[asyncio.Queue, "_Unsubscribe"]:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self.queues.append(q)

        async def unsubscribe() -> None:
            async with self._lock:
                if q in self.queues:
                    self.queues.remove(q)

        return q, unsubscribe


# 类型别名：取消订阅 callable 的形态
_Unsubscribe = "AsyncIterable[None]"  # 仅注释用；实际是 async () -> None


class SystemEventBus:
    """按 ``session_id`` 索引的进程内 pub/sub 总线。

    多 worker 部署时各自维护一份；总线只是"实时性优化"，**核心保证靠 DB
    持久化**——某 worker 没收到 publish 也不影响订阅者刷新页面后的行为。
    """

    def __init__(self) -> None:
        self._channels: dict[uuid.UUID, _SessionChannel] = {}
        self._lock = asyncio.Lock()

    async def _channel(self, session_id: uuid.UUID) -> _SessionChannel:
        async with self._lock:
            ch = self._channels.get(session_id)
            if ch is None:
                ch = _SessionChannel()
                self._channels[session_id] = ch
            return ch

    async def publish(self, session_id: uuid.UUID, event: dict[str, Any]) -> None:
        ch = await self._channel(session_id)
        await ch.publish(event)

    async def subscribe(
        self, session_id: uuid.UUID,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """订阅指定 session 的系统事件流；调用方需要在 finally 里耗尽该 generator
        以触发 unsubscribe。
        """
        ch = await self._channel(session_id)
        q, unsubscribe = await ch.subscribe()
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            await unsubscribe()


SYSTEM_EVENT_BUS = SystemEventBus()


# ─────────────────── 完成回流（"任务跑完了"系统消息） ────────────────


def _format_done_content(result: dict[str, Any]) -> str:
    """把执行结果摘要拼成一条 chat 气泡可读的中文 Markdown。

    - 状态优先级：``status`` 字段；``passed_cases / failed_cases / total_cases``
      存在时拼"X/Y 通过"。
    - 不含敏感字段（key / token / api_key）；调用方负责 mask 后再传入。
    """
    status = (result.get("status") or "").lower()
    title = result.get("title") or "UI 自动化任务"
    icon = "✅" if status in ("passed", "success", "succeeded") else (
        "❌" if status in ("failed", "error") else "⏱"
    )
    lines = [f"{icon} **{title}** 已完成"]
    duration_ms = result.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        secs = int(duration_ms / 1000)
        if secs >= 60:
            lines.append(f"耗时：{secs // 60} 分 {secs % 60} 秒")
        else:
            lines.append(f"耗时：{secs} 秒")
    total = result.get("total_cases")
    passed = result.get("passed_cases")
    failed = result.get("failed_cases")
    if isinstance(total, int) and total > 0:
        passed_n = passed if isinstance(passed, int) else 0
        failed_n = failed if isinstance(failed, int) else 0
        lines.append(f"用例：{passed_n} 通过 / {failed_n} 失败 / 共 {total}")
    err = result.get("error_message")
    if err:
        lines.append(f"错误：{err}")
    return "\n\n".join(lines)


async def publish_execution_done(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    task_id: uuid.UUID,
    result: dict[str, Any],
) -> ChatMessage | None:
    """落一条 ``kind='execution_event'`` 的系统消息 + 总线广播。

    - 校验 ``session_id`` 存在；不存在 ⇒ 返回 ``None``（任务可能在临时会话里
      派发后会话被删，丢失广播无害——前端打开任务详情仍能从
      ``ui_executions`` 表看到完整结果）。
    - 写完 commit；之后再发布到总线（先 commit 再 publish 保证落库 happens-
      before；订阅端拉到事件后再去 GET messages 不会读到悬空 ID）。
    """
    session = await db.get(ChatSession, session_id)
    if session is None:
        logger.info(
            "publish_execution_done: chat session %s no longer exists; skipping",
            session_id,
        )
        return None

    content = _format_done_content(result)
    payload = {
        "task_id": str(task_id),
        "result": result,
    }
    msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=content,
        kind="execution_event",
        meta_data=payload,
    )
    db.add(msg)
    await db.flush()
    await db.commit()
    await db.refresh(msg)

    event = {
        "kind": "execution_event",
        "session_id": str(session_id),
        "task_id": str(task_id),
        "message_id": str(msg.id),
        "result": result,
        "content": content,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
    try:
        await SYSTEM_EVENT_BUS.publish(session_id, event)
    except Exception:  # noqa: BLE001
        # 总线广播失败不能影响落库；总线只是实时性优化。
        logger.exception("publish_execution_done: bus publish failed; DB row already committed")

    return msg


# ─────────────────── 确认卡片回流（"AI 提议执行计划"系统消息） ───────────


async def publish_skill_card(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    plan_id: uuid.UUID,
    plan_payload: dict[str, Any],
    summary_text: str | None = None,
) -> ChatMessage | None:
    """落一条 ``kind='skill_card'`` 的 chat message + 总线广播。

    被 ``system__ui_automation__propose_execution_plan`` 在生成 plan 后调用，
    把整张 ConfirmationCard payload 持久化到目标 session（前端按 ``kind`` 分发
    到 ``ConfirmationCard.vue`` 渲染）。``plan_id`` 同时塞到 ``meta_data`` 里，
    前端"确认执行"调 ``POST /ui-executions { plan_id }`` 时只送此 id，避免
    用户篡改卡片字段冒充已确认 plan（设计 §10.3.3 安全闸门）。

    Phase 13 / Task 13.3：本函数与 ``publish_execution_done`` 共用同一根
    SYSTEM_EVENT_BUS；前端订阅 SSE 后会同时收到两类事件，按 ``kind`` 切渲染。
    """
    session = await db.get(ChatSession, session_id)
    if session is None:
        logger.info(
            "publish_skill_card: chat session %s no longer exists; skipping",
            session_id,
        )
        return None

    content = summary_text or "已为你拟定执行计划，请在上方卡片确认或修改。"
    meta_data = {
        "action_type": "skill_card",
        "plan_id": str(plan_id),
        "plan": plan_payload,
    }
    msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=content,
        kind="skill_card",
        meta_data=meta_data,
    )
    db.add(msg)
    await db.flush()
    await db.commit()
    await db.refresh(msg)

    event = {
        "kind": "skill_card",
        "session_id": str(session_id),
        "plan_id": str(plan_id),
        "message_id": str(msg.id),
        "plan": plan_payload,
        "content": content,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
    try:
        await SYSTEM_EVENT_BUS.publish(session_id, event)
    except Exception:  # noqa: BLE001
        logger.exception("publish_skill_card: bus publish failed; DB row already committed")
    return msg


async def publish_task_status(
    *,
    session_id: uuid.UUID,
    task_id: uuid.UUID,
    status: str,
    progress: dict[str, Any] | None = None,
) -> None:
    """轻量"任务态变更"广播（不落 chat_messages）。

    用于 ``TaskBadge`` 在 chat SSE 流里增量刷新进度（pending → running →
    completed）。**不落库**——浏览器重连后由前端调
    ``GET /api/ui-executions/{id}`` 重新拉最新态，避免 chat_messages 表被噪声
    填满。终态时上层应额外调 ``publish_execution_done`` 落一条总结消息。
    """
    event = {
        "kind": "task_status",
        "session_id": str(session_id),
        "task_id": str(task_id),
        "status": status,
        "progress": progress or {},
    }
    try:
        await SYSTEM_EVENT_BUS.publish(session_id, event)
    except Exception:  # noqa: BLE001
        logger.exception("publish_task_status: bus publish failed")


__all__ = [
    "SYSTEM_EVENT_BUS",
    "SystemEventBus",
    "publish_execution_done",
    "publish_skill_card",
    "publish_task_status",
]
