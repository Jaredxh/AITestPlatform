"""Task 7.1 验证：ExecutionStreamHub 行为契约。

覆盖三个核心场景（对应文档 Task 7.1 的"验证方式"）：
1. 写一个 fake task 周期性 publish 事件 → subscriber 实时拿到
2. 重连：subscriber 中途断开 → 再次 subscribe(from_idx=0) 拿到完整事件流
3. done / mark_done 后 subscribe 不会永远阻塞
4. 30 分钟过期 evict 工作正确（用 monkeypatch 模拟时间）
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.modules.ui_automation import stream_hub
from app.modules.ui_automation.stream_hub import (
    EXECUTION_STREAM_HUB,
    _ExecutionStream,
    _ExecutionStreamHub,
)


@pytest.fixture
def fresh_hub() -> _ExecutionStreamHub:
    """每条测试用独立 hub 实例，避免单例共享导致测试间污染。"""
    return _ExecutionStreamHub()


async def test_register_returns_new_stream(fresh_hub: _ExecutionStreamHub) -> None:
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)
    assert isinstance(stream, _ExecutionStream)
    assert fresh_hub.get(eid) is stream


async def test_get_missing_returns_none(fresh_hub: _ExecutionStreamHub) -> None:
    assert fresh_hub.get(uuid.uuid4()) is None


async def test_append_and_realtime_subscribe(
    fresh_hub: _ExecutionStreamHub,
) -> None:
    """模拟"fake task 周期 publish + 客户端订阅"场景。"""
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)

    received: list[tuple[str, dict]] = []

    async def consumer() -> None:
        async for event in stream.subscribe():
            received.append(event)

    consumer_task = asyncio.create_task(consumer())
    # 让 consumer 先进入 wait
    await asyncio.sleep(0)

    await stream.append("step_progress", {"step": 1, "action": "open page"})
    await stream.append("step_progress", {"step": 2, "action": "type username"})
    await stream.append("done", {"execution_id": str(eid)})

    await asyncio.wait_for(consumer_task, timeout=1.0)

    assert [name for name, _ in received] == ["step_progress", "step_progress", "done"]
    assert received[0][1]["step"] == 1
    assert received[2][1]["execution_id"] == str(eid)
    assert stream.done is True


async def test_resubscribe_replays_all_events(
    fresh_hub: _ExecutionStreamHub,
) -> None:
    """关键场景：客户端 Ctrl+C 再 curl 一次 → 必须从头看完整事件流。"""
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)

    # 先全部 publish 完
    await stream.append("execution_start", {"total_cases": 1})
    await stream.append("step_progress", {"step": 1})
    await stream.append("step_progress", {"step": 2})
    await stream.append("done", {})

    # 此时 subscribe(from_idx=0) 应该一次性拿到全部 4 条
    received: list[str] = []
    async for name, _data in stream.subscribe(from_idx=0):
        received.append(name)

    assert received == ["execution_start", "step_progress", "step_progress", "done"]


async def test_concurrent_subscribers_each_get_full_stream(
    fresh_hub: _ExecutionStreamHub,
) -> None:
    """两个 tab 同时订阅同一个 execution，各自都拿到全部事件。"""
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)

    async def collect() -> list[str]:
        out: list[str] = []
        async for name, _ in stream.subscribe():
            out.append(name)
        return out

    a = asyncio.create_task(collect())
    b = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await stream.append("step_progress", {"step": 1})
    await stream.append("step_progress", {"step": 2})
    await stream.mark_done()

    a_events, b_events = await asyncio.gather(
        asyncio.wait_for(a, timeout=1.0),
        asyncio.wait_for(b, timeout=1.0),
    )
    assert a_events == ["step_progress", "step_progress"] == b_events


async def test_mark_done_unblocks_pending_subscribers(
    fresh_hub: _ExecutionStreamHub,
) -> None:
    """没有事件 + mark_done 后，subscribe 应当立即返回，不会永远阻塞。"""
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)

    async def consumer() -> int:
        count = 0
        async for _ in stream.subscribe():
            count += 1
        return count

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    await stream.mark_done()
    count = await asyncio.wait_for(consumer_task, timeout=1.0)
    assert count == 0  # 没有事件被 emit


async def test_terminal_error_event_marks_done(
    fresh_hub: _ExecutionStreamHub,
) -> None:
    """error_terminal 事件应当与 done 一样让 stream 自动终态化。"""
    eid = uuid.uuid4()
    stream = await fresh_hub.register(eid)

    await stream.append("error_terminal", {"message": "browser crashed"})
    assert stream.done is True

    received: list[str] = []
    async for name, _ in stream.subscribe(from_idx=0):
        received.append(name)
    assert received == ["error_terminal"]


async def test_unregister_removes_stream(fresh_hub: _ExecutionStreamHub) -> None:
    eid = uuid.uuid4()
    await fresh_hub.register(eid)
    assert fresh_hub.get(eid) is not None
    await fresh_hub.unregister(eid)
    assert fresh_hub.get(eid) is None


async def test_evict_stale_removes_done_streams(
    fresh_hub: _ExecutionStreamHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟时间前进 30 分钟 + 1 秒，验证已 done 的流被清掉，
    未 done 的流被保留（避免误删运行中的执行）。"""
    fake_now = [1000.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr(stream_hub.time, "monotonic", fake_monotonic)

    done_eid = uuid.uuid4()
    live_eid = uuid.uuid4()

    done_stream = await fresh_hub.register(done_eid)
    live_stream = await fresh_hub.register(live_eid)
    await done_stream.mark_done()
    # live_stream 故意不 done，模拟正在跑

    # 时钟跳到 30 分零 1 秒后
    fake_now[0] = 1000.0 + 30 * 60 + 1

    # register 一个新的 stream 触发 evict
    trigger_eid = uuid.uuid4()
    await fresh_hub.register(trigger_eid)

    assert fresh_hub.get(done_eid) is None, "已 done 且过期的流应被清理"
    assert fresh_hub.get(live_eid) is live_stream, "未 done 的流不应被清理"
    assert fresh_hub.get(trigger_eid) is not None


async def test_module_singleton_is_executiontreamhub() -> None:
    """模块级单例存在且类型正确（防止 import 路径出错）。"""
    assert isinstance(EXECUTION_STREAM_HUB, _ExecutionStreamHub)
