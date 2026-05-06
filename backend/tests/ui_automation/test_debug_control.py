"""Task 9.7 — debug_control.py 单测。

不依赖 DB / Engine，只测 ``_DebugControlHub`` 的状态机：register / signal /
wait_for_continue 三种 outcome（continue / timeout / stopped）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.modules.ui_automation.debug_control import _DebugControlHub

# ─── register / get / unregister 基础 ────────────────────────────────


@pytest.mark.asyncio
async def test_register_and_get() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    sig = await hub.register(eid)
    assert hub.get(eid) is sig
    assert hub.get(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_unregister() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)
    await hub.unregister(eid)
    assert hub.get(eid) is None
    # 二次 unregister 不报错（幂等）
    await hub.unregister(eid)


@pytest.mark.asyncio
async def test_register_twice_replaces_signal() -> None:
    """重复 register 同一个 id：覆盖旧 signal —— 防 stale event 误推进。"""
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    sig1 = await hub.register(eid)
    sig2 = await hub.register(eid)
    assert sig1 is not sig2
    assert hub.get(eid) is sig2


# ─── signal_continue ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_continue_returns_false_for_unregistered() -> None:
    hub = _DebugControlHub()
    delivered = await hub.signal_continue(uuid.uuid4())
    assert delivered is False


@pytest.mark.asyncio
async def test_signal_continue_returns_true_for_registered() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)
    assert await hub.signal_continue(eid) is True


# ─── wait_for_continue: continue 路径 ────────────────────────────────


@pytest.mark.asyncio
async def test_wait_returns_continue_when_signal_arrives() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)

    async def signaller():
        # 给 wait 一点起步时间
        await asyncio.sleep(0.05)
        await hub.signal_continue(eid)

    asyncio.create_task(signaller())
    outcome = await hub.wait_for_continue(eid, timeout=2.0, poll_interval=0.02)
    assert outcome == "continue"


@pytest.mark.asyncio
async def test_wait_for_unregistered_returns_continue_immediately() -> None:
    """没注册的 execution_id wait → 不能永远卡住，应直接 continue 兜底。"""
    hub = _DebugControlHub()
    outcome = await hub.wait_for_continue(uuid.uuid4(), timeout=0.5)
    assert outcome == "continue"


# ─── wait_for_continue: timeout 路径 ─────────────────────────────────


@pytest.mark.asyncio
async def test_wait_returns_timeout_when_no_signal() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)
    outcome = await hub.wait_for_continue(eid, timeout=0.1, poll_interval=0.02)
    assert outcome == "timeout"


# ─── wait_for_continue: stopped 路径 ─────────────────────────────────


@pytest.mark.asyncio
async def test_wait_returns_stopped_when_stop_check_returns_true() -> None:
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)

    call_count = {"n": 0}

    async def stop_check() -> bool:
        call_count["n"] += 1
        # 第三次 poll 返回 True
        return call_count["n"] >= 3

    outcome = await hub.wait_for_continue(
        eid, timeout=2.0, stop_check=stop_check, poll_interval=0.02,
    )
    assert outcome == "stopped"
    assert call_count["n"] >= 3


@pytest.mark.asyncio
async def test_wait_stop_check_failure_does_not_break_wait() -> None:
    """stop_check 异常时不能让 Engine 卡住——继续等下一轮 poll。"""
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)

    async def boom() -> bool:
        raise RuntimeError("DB connection lost")

    # 在 timeout 短时间内信号应到，否则会 timeout（也是合法行为）
    async def signaller():
        await asyncio.sleep(0.05)
        await hub.signal_continue(eid)

    asyncio.create_task(signaller())
    outcome = await hub.wait_for_continue(
        eid, timeout=2.0, stop_check=boom, poll_interval=0.02,
    )
    assert outcome == "continue"


# ─── stale signal clear ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_clears_previous_signal() -> None:
    """signal 后再 wait —— wait 应等新信号，而不是被上轮的 sticky event 立刻
    返回 continue。这是 step-by-step debug 的关键正确性保证。
    """
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)

    # 先发一个 signal（模拟"上一步"）
    await hub.signal_continue(eid)

    # 现在 wait_for_continue：应该 timeout（因为 wait 入口会 clear）
    outcome = await hub.wait_for_continue(eid, timeout=0.1, poll_interval=0.02)
    assert outcome == "timeout"


@pytest.mark.asyncio
async def test_full_pause_continue_loop() -> None:
    """模拟 Engine 跑 3 步 debug 模式：每步 wait → 收到 continue → 推进。"""
    hub = _DebugControlHub()
    eid = uuid.uuid4()
    await hub.register(eid)

    outcomes: list[str] = []

    async def engine_loop():
        for _ in range(3):
            o = await hub.wait_for_continue(eid, timeout=2.0, poll_interval=0.02)
            outcomes.append(o)

    async def user_clicks():
        for _ in range(3):
            await asyncio.sleep(0.05)
            await hub.signal_continue(eid)

    await asyncio.gather(engine_loop(), user_clicks())
    assert outcomes == ["continue", "continue", "continue"]
