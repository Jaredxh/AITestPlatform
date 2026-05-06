"""ExecutionStreamHub — UI 自动化执行的进程内 pub-sub 广播器。

直接复刻一期 ``chat_service._ChatStream / _ChatStreamHub`` 的设计。一期已经
验证 in-process + asyncio.Condition 模式可以扛住"刷新页面 / 切换 tab / 短暂
断网后再 curl"等典型 SSE 重连场景；UI 自动化执行同样跑后台 asyncio 任务，
完全可以套同一套机制，因此**不需要**为二期专门引入 Redis / ARQ。

后续如果要做"任务跨进程恢复 / 多 worker 弹性扩容"再升级（见 Task 11.4
ARQ + Worker 容器，可选增强）；届时只需把 ``_ExecutionStream`` 内部的
asyncio.Condition 换成 Redis Pub/Sub 订阅迭代器，对 router 层的 SSE 端点
保持零改动。

关键约束（与一期 chat hub 对齐）：
- key 是 ``execution_id``（``uuid.UUID``），全平台唯一
- 事件结构是 ``(event_name: str, data: dict)`` 元组
- subscribe 支持 ``from_idx``，重连时从 0 开始能拿到全部历史事件
- ``done=True`` 后 subscribe 会自然结束（不会永远 await）
- 30 分钟 evict 已 done 的流，避免内存泄漏（与一期完全一致）
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator

# 已 done 的 stream 在内存里保留多久后自动清理。与一期 chat hub 对齐 30 分钟，
# 这个值要 > 用户"刷新页面后再次回看完成历史"的典型行为窗口，否则会把"刚跑完
# 想看看的"那批用户挡在 DB 回放分支里。
_STALE_EVICT_SECONDS = 30 * 60


class _ExecutionStream:
    """单次 UI 自动化执行的事件缓冲广播器。

    生产端（ExecutionEngine 后台任务）调用 ``append`` 不断推入事件；消费端
    （SSE 端点 / chat orchestrator）调用 ``subscribe`` 拿到 async generator。

    多个消费端可同时订阅同一个 stream（典型场景：用户开了两个 tab 看同一个
    execution）；每个 subscribe 调用都从 ``from_idx`` 开始独立游标推进，互不
    影响。

    线程安全：通过 ``asyncio.Condition`` 在事件循环内做协程级同步，不需要
    跨线程锁——因为 backend 进程内所有任务都跑在同一个事件循环上。
    """

    __slots__ = ("chunks", "done", "_cond", "created_at")

    def __init__(self) -> None:
        self.chunks: list[tuple[str, dict]] = []
        self.done: bool = False
        self._cond: asyncio.Condition = asyncio.Condition()
        self.created_at: float = time.monotonic()

    async def append(self, event: str, data: dict) -> None:
        """推入一条事件并唤醒所有 subscriber。

        约定：``done`` / ``error_terminal`` 视为终态，自动把 ``self.done``
        置位，subscriber 拿到这条事件后下一轮 wait 会立即返回。
        """
        async with self._cond:
            self.chunks.append((event, data))
            if event in ("done", "error_terminal"):
                self.done = True
            self._cond.notify_all()

    async def mark_done(self) -> None:
        """显式标记 stream 已结束（在 finally 里调，确保 subscriber 不会
        永远 await）。和 ``append("done", ...)`` 互补，但不会再写入新事件。
        """
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(
        self, from_idx: int = 0
    ) -> AsyncGenerator[tuple[str, dict], None]:
        """从 ``from_idx`` 开始迭代历史 + 实时事件。

        - ``from_idx=0``（默认）：客户端首次连接 / 重连后想要看完整事件流
        - ``from_idx=N``：极少使用；只有客户端能可靠记住自己上次看到的 idx
          时才需要（一期目前所有调用方都是 0，二期建议保持）
        """
        while True:
            async with self._cond:
                while from_idx >= len(self.chunks):
                    if self.done:
                        return
                    await self._cond.wait()
                chunk = self.chunks[from_idx]
            from_idx += 1
            yield chunk


class _ExecutionStreamHub:
    """``execution_id → _ExecutionStream`` 注册表。

    用 ``asyncio.Lock`` 保护 ``_store`` 增删，使 register / unregister /
    evict 这些写操作互斥；``get`` 是只读，不需要持锁（dict.get 在 CPython
    层面是原子的）。
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, _ExecutionStream] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def register(self, execution_id: uuid.UUID) -> _ExecutionStream:
        """为 ``execution_id`` 注册一个新的 stream。

        必须在派发后台任务**之前**调用，否则可能出现：客户端抢先连到 SSE
        端点 → ``hub.get`` 拿到 None → 走"DB 回放分支" → 错过实时事件。
        """
        async with self._lock:
            stream = _ExecutionStream()
            self._store[execution_id] = stream
            self._evict_stale_locked()
            return stream

    def get(self, execution_id: uuid.UUID) -> _ExecutionStream | None:
        """查询 stream；找不到返回 None（典型：任务已完成且被 evict / 服务
        重启）。调用方应在 None 时降级到 DB 回放最终状态。
        """
        return self._store.get(execution_id)

    async def unregister(self, execution_id: uuid.UUID) -> None:
        """主动从 hub 删除某个 stream。一般不需要调，依赖 30 分钟 evict
        即可；只有删除 execution 等需要立刻释放内存时才用。
        """
        async with self._lock:
            self._store.pop(execution_id, None)

    def _evict_stale_locked(self) -> None:
        """清理 30 分钟前已完成的流，避免内存无限增长。

        必须在持有 ``self._lock`` 的上下文中调（方法名末尾的 _locked 是
        Python 社区惯例，提示调用者注意锁约束）。
        """
        cutoff = time.monotonic() - _STALE_EVICT_SECONDS
        stale = [
            eid for eid, s in self._store.items()
            if s.done and s.created_at < cutoff
        ]
        for eid in stale:
            self._store.pop(eid, None)


EXECUTION_STREAM_HUB = _ExecutionStreamHub()
"""模块级单例。所有 router / service 都应通过它来 register / get。"""
