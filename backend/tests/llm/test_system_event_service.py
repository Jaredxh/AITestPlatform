"""Phase 13 / Task 13.0 — system_event_service 单测。

DoD：
- ``publish_execution_done`` 写入一条 ``kind='execution_event'`` 消息
- 通过 SSE 总线把事件推到该 session 所有在线连接（订阅者能拉到）
- session 不存在时优雅返回 None（不抛异常）
- 总线广播失败不影响 DB 落库（异常 swallow）
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.llm import system_event_service
from app.modules.llm.models import ChatMessage, ChatSession
from app.modules.llm.system_event_service import (
    SYSTEM_EVENT_BUS,
    SystemEventBus,
    publish_execution_done,
    publish_skill_card,
    publish_task_status,
)

# ─────────────────── 总线 pub/sub 自身行为 ───────────────────


@pytest.mark.asyncio
async def test_bus_subscriber_receives_published_event() -> None:
    bus = SystemEventBus()
    sid = uuid.uuid4()

    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for ev in bus.subscribe(sid):
            received.append(ev)
            if len(received) >= 1:
                return

    task = asyncio.create_task(consume())
    # 给订阅者一个调度窗口去拿 channel + queue
    await asyncio.sleep(0)
    await bus.publish(sid, {"kind": "execution_event", "task_id": "x"})
    await asyncio.wait_for(task, timeout=2.0)

    assert received == [{"kind": "execution_event", "task_id": "x"}]


@pytest.mark.asyncio
async def test_bus_isolates_sessions() -> None:
    bus = SystemEventBus()
    a, b = uuid.uuid4(), uuid.uuid4()

    received_a: list[dict] = []

    async def consume_a() -> None:
        async for ev in bus.subscribe(a):
            received_a.append(ev)
            return

    task = asyncio.create_task(consume_a())
    await asyncio.sleep(0)
    # 发到 b：a 的订阅者不应收到
    await bus.publish(b, {"kind": "execution_event", "task_id": "wrong"})
    # 真正发到 a
    await bus.publish(a, {"kind": "execution_event", "task_id": "right"})
    await asyncio.wait_for(task, timeout=2.0)
    assert received_a == [{"kind": "execution_event", "task_id": "right"}]


# ─────────────────── publish_execution_done ───────────────────


class _FakeAsyncSession:
    """最小 fake session 用于隔离 ORM 行为；不连 PG。"""

    def __init__(self, *, session_obj: ChatSession | None) -> None:
        self._session_obj = session_obj
        self.added: list[ChatMessage] = []
        self.commits = 0
        self.flushes = 0

    async def get(self, model: type, _id):  # noqa: ANN001
        if model is ChatSession:
            return self._session_obj
        return None

    def add(self, row) -> None:  # noqa: ANN001
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1
        for r in self.added:
            if getattr(r, "id", None) is None:
                r.id = uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row) -> None:  # noqa: ANN001
        return None


@pytest.mark.asyncio
async def test_publish_execution_done_persists_message_and_publishes_to_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    tid = uuid.uuid4()
    chat_session = ChatSession(id=sid, project_id=uuid.uuid4(), user_id=uuid.uuid4(), title="x")
    db = _FakeAsyncSession(session_obj=chat_session)

    # 隔离全局 bus 行为：换一个本地 bus，验证 publish 被调用
    local_bus = SystemEventBus()
    monkeypatch.setattr(system_event_service, "SYSTEM_EVENT_BUS", local_bus)

    received: list[dict] = []

    async def consume() -> None:
        async for ev in local_bus.subscribe(sid):
            received.append(ev)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)

    msg = await publish_execution_done(
        db,  # type: ignore[arg-type]
        session_id=sid,
        task_id=tid,
        result={
            "title": "登录-验证账号密码",
            "status": "passed",
            "duration_ms": 128_000,
            "total_cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
        },
    )

    await asyncio.wait_for(task, timeout=2.0)

    assert msg is not None
    assert isinstance(msg, ChatMessage)
    assert msg.kind == "execution_event"
    assert msg.session_id == sid
    assert msg.role == "assistant"
    # meta_data 含结构化 payload，便于前端组件渲染按钮
    assert msg.meta_data is not None
    assert msg.meta_data["task_id"] == str(tid)
    # 内容含图标 + 标题 + 用例统计
    assert "登录-验证账号密码" in msg.content
    assert "✅" in msg.content

    # 落库：flush + commit 至少一次
    assert db.commits >= 1
    assert msg in db.added

    # 总线收到事件
    assert len(received) == 1
    assert received[0]["session_id"] == str(sid)
    assert received[0]["task_id"] == str(tid)
    assert received[0]["message_id"] == str(msg.id)


@pytest.mark.asyncio
async def test_publish_execution_done_returns_none_when_session_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    tid = uuid.uuid4()
    db = _FakeAsyncSession(session_obj=None)

    bus_publish = AsyncMock()
    monkeypatch.setattr(SYSTEM_EVENT_BUS, "publish", bus_publish)

    msg = await publish_execution_done(
        db,  # type: ignore[arg-type]
        session_id=sid,
        task_id=tid,
        result={"status": "failed"},
    )
    assert msg is None
    # 不写消息、不发布到总线
    assert db.added == []
    bus_publish.assert_not_called()


@pytest.mark.asyncio
async def test_publish_execution_done_swallows_bus_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    chat_session = ChatSession(id=sid, project_id=uuid.uuid4(), user_id=uuid.uuid4())
    db = _FakeAsyncSession(session_obj=chat_session)

    failing_bus = MagicMock()
    failing_bus.publish = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(system_event_service, "SYSTEM_EVENT_BUS", failing_bus)

    # 总线异常不应影响落库（DoD：事件落库即送达）
    msg = await publish_execution_done(
        db,  # type: ignore[arg-type]
        session_id=sid,
        task_id=uuid.uuid4(),
        result={"status": "failed"},
    )
    assert msg is not None
    assert msg.kind == "execution_event"
    assert db.commits >= 1


# ─────────────────── publish_skill_card / publish_task_status ───────────────────


@pytest.mark.asyncio
async def test_publish_skill_card_persists_message_and_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = uuid.uuid4()
    plan_id = uuid.uuid4()
    chat_session = ChatSession(id=sid, project_id=uuid.uuid4(), user_id=uuid.uuid4(), title="x")
    db = _FakeAsyncSession(session_obj=chat_session)

    local_bus = SystemEventBus()
    monkeypatch.setattr(system_event_service, "SYSTEM_EVENT_BUS", local_bus)

    received: list[dict] = []

    async def consume() -> None:
        async for ev in local_bus.subscribe(sid):
            received.append(ev)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)

    plan_payload = {
        "plan_id": str(plan_id),
        "cases": [{"case_no": 1, "title": "登录"}],
    }
    msg = await publish_skill_card(
        db,  # type: ignore[arg-type]
        session_id=sid,
        plan_id=plan_id,
        plan_payload=plan_payload,
    )
    await asyncio.wait_for(task, timeout=2.0)

    assert msg is not None
    assert msg.kind == "skill_card"
    assert msg.role == "assistant"
    assert msg.meta_data is not None
    assert msg.meta_data["action_type"] == "skill_card"
    assert msg.meta_data["plan_id"] == str(plan_id)
    assert msg.meta_data["plan"] == plan_payload
    assert db.commits >= 1

    # 总线收到 skill_card 事件
    assert len(received) == 1
    assert received[0]["kind"] == "skill_card"
    assert received[0]["plan_id"] == str(plan_id)
    assert received[0]["message_id"] == str(msg.id)


@pytest.mark.asyncio
async def test_publish_skill_card_returns_none_when_session_gone() -> None:
    db = _FakeAsyncSession(session_obj=None)
    msg = await publish_skill_card(
        db,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        plan_payload={},
    )
    assert msg is None
    assert db.added == []


@pytest.mark.asyncio
async def test_publish_task_status_uses_bus_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sid = uuid.uuid4()
    tid = uuid.uuid4()

    local_bus = SystemEventBus()
    monkeypatch.setattr(system_event_service, "SYSTEM_EVENT_BUS", local_bus)

    received: list[dict] = []

    async def consume() -> None:
        async for ev in local_bus.subscribe(sid):
            received.append(ev)
            return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)

    await publish_task_status(
        session_id=sid,
        task_id=tid,
        status="running",
        progress={"passed_cases": 1, "total_cases": 3},
    )
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0]["kind"] == "task_status"
    assert received[0]["task_id"] == str(tid)
    assert received[0]["status"] == "running"
    assert received[0]["progress"]["passed_cases"] == 1
