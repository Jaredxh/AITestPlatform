"""Debug 模式的"暂停-继续"信号 hub（Task 9.7）。

设计思路（与 ``stream_hub.py`` 同构）：
- 进程内单例 ``DEBUG_CONTROL_HUB``，``execution_id → asyncio.Event``
- ``ExecutionEngine`` 在 ``mode="debug"`` 跑完每个 step 后调
  ``await wait_for_continue(eid, timeout=1800)``：
  · 30 分钟内有人 ``POST /continue`` → 返回 ``"continue"``，循环推进
  · 30 分钟无信号 → 返回 ``"timeout"``，Engine 把 execution 标记 stopped
  · 用户中途 ``POST /stop`` → 返回 ``"stopped"``，Engine 走正常 stop 路径
- ``register / unregister`` 由 Engine 的 setup / finally 自动管理；外部不需要手动调

为什么不复用 ``EXECUTION_STREAM_HUB``？
- stream_hub 是"广播事件流"，多个订阅者都能收到，且事件不会被消费掉
- debug 信号是"一次性 fire-and-forget"，需要一个真正的 ``asyncio.Event``
  来支持 wait+timeout；语义和 stream 完全不同，混在一起会让两者的接口都
  变畸形。另起一个小 hub 反而更干净。

测试约束：
- 不能依赖真实 30 分钟超时；测试时 patch ``DEFAULT_DEBUG_TIMEOUT_SECONDS``
  为 0.05 秒级别即可
- ``stop_check`` 是个 callable，便于注入 stub（生产环境就是
  ``persistence.is_execution_stopped``）
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

logger = logging.getLogger(__name__)

# 30 分钟硬上限——超过就自动 stop 释放浏览器实例。
# 这个值要 >> 最长合理"想再看看 snapshot"的人类思考时间，但 << 一晚上
# 不能让 1 个掉线用户卡住一个 Chromium 进程
DEFAULT_DEBUG_TIMEOUT_SECONDS: float = 30 * 60

# 在 wait 期间多久轮询一次 stop_check。设小则响应快，设大省 CPU；50ms 足够
# 让 ``POST /stop`` 在用户感知不到的时延内被注意到（< 1 帧）
_STOP_POLL_INTERVAL_SECONDS: float = 0.05


WaitOutcome = Literal["continue", "timeout", "stopped"]


class _DebugSignal:
    """单次 execution 的 continue 信号封装。

    用 ``asyncio.Event`` + ``set()`` 通知；``signal_continue`` 后
    ``wait_for_continue`` 立刻 wake。signal 是 sticky 的——signal 后再调
    ``clear()`` 才能下一次 pause-wait。Engine 每跑完一步会先 ``clear()`` 再
    ``wait_for_continue``，所以"上一轮的 signal"不会泄漏到下一轮。
    """

    __slots__ = ("event", "created_at")

    def __init__(self) -> None:
        self.event: asyncio.Event = asyncio.Event()
        self.created_at: float = time.monotonic()


class _DebugControlHub:
    """``execution_id → _DebugSignal`` 注册表（进程内）。

    与 ``ExecutionStreamHub`` 一样，假设所有协程都在同一个事件循环里跑——
    backend 单进程异步模型成立时这个假设不会被打破；多 worker 部署时本 hub
    需要切到 Redis Pub/Sub（与 stream_hub 同步升级，Task 11.4 范围）。
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, _DebugSignal] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def register(self, execution_id: uuid.UUID) -> _DebugSignal:
        """为 ``execution_id`` 注册一个 debug signal。

        重复 register 会覆盖（用新 Event 替掉旧的）；典型场景是 ``stop +
        retry-failed`` 复用同一个 id（实际不会——retry 会生成新 id，但代码
        要 future-proof）。
        """
        async with self._lock:
            sig = _DebugSignal()
            self._store[execution_id] = sig
            return sig

    def get(self, execution_id: uuid.UUID) -> _DebugSignal | None:
        return self._store.get(execution_id)

    async def unregister(self, execution_id: uuid.UUID) -> None:
        async with self._lock:
            self._store.pop(execution_id, None)

    async def signal_continue(self, execution_id: uuid.UUID) -> bool:
        """触发 continue 信号；返回 True=信号送达，False=execution 不在 debug
        中（典型：误点了 continue 按钮 / 已经超时被卸了）。
        """
        sig = self._store.get(execution_id)
        if sig is None:
            return False
        sig.event.set()
        return True

    async def wait_for_continue(
        self,
        execution_id: uuid.UUID,
        *,
        timeout: float = DEFAULT_DEBUG_TIMEOUT_SECONDS,
        stop_check: Callable[[], Awaitable[bool]] | None = None,
        poll_interval: float = _STOP_POLL_INTERVAL_SECONDS,
    ) -> WaitOutcome:
        """阻塞等 continue 信号。

        三种返回：
        - ``"continue"``：用户调了 ``POST /continue``，Engine 推进下一步
        - ``"timeout"``：超过 ``timeout`` 秒未收到，Engine 应自动 stop
        - ``"stopped"``：``stop_check`` 返回 True（用户主动 stop），Engine
          走正常 stop 路径

        实现细节：用"短超时 + 循环"实现可中断 wait —— 不用一次性
        ``wait_for(event, timeout)`` 是因为那样的话 stop 信号要等到 timeout
        才会被发现，体验差。``poll_interval=50ms`` 在 CPU 与响应延迟之间取折衷。
        """
        sig = self._store.get(execution_id)
        if sig is None:
            # 没注册却被调 → 视为已 continue（不能让 Engine 永远卡住）
            logger.warning(
                "wait_for_continue: %s not registered, returning continue immediately",
                execution_id,
            )
            return "continue"

        # 上一轮可能 set 过；这里 clear 一下保证本轮真要等新信号
        sig.event.clear()

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if stop_check is not None:
                try:
                    if await stop_check():
                        return "stopped"
                except Exception:  # noqa: BLE001
                    # stop_check 失败时不阻塞 debug —— 当作没 stop 处理；
                    # Engine 还有最外层 ``_check_stopped`` 兜底
                    logger.exception("debug.wait_for_continue stop_check raised")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"

            wait_slice = min(poll_interval, remaining)
            try:
                await asyncio.wait_for(sig.event.wait(), timeout=wait_slice)
            except asyncio.TimeoutError:
                continue
            return "continue"


DEBUG_CONTROL_HUB = _DebugControlHub()
"""模块级单例。Engine + router 都通过它通信。"""


__all__ = [
    "DEBUG_CONTROL_HUB",
    "DEFAULT_DEBUG_TIMEOUT_SECONDS",
    "WaitOutcome",
    "_DebugControlHub",
    "_DebugSignal",
]
