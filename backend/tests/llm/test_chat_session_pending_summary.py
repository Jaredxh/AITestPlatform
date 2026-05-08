"""Phase 13 / Task 13.3 — chat_service.get_pending_task_summary 单测。

目标：
- 仅返回 ``kind='execution_event'`` 的最近 20 条消息
- 校验 session 存在 + 当前用户拥有该 session
- count 与 items 长度一致；items 含 task_id / result / message_id 字段
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.modules.llm import chat_service
from app.modules.llm.models import ChatMessage, ChatSession


class _ResultStub:
    def __init__(self, *, items=None, scalar=None):
        self._items = items or []
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._items)

    def scalar_one_or_none(self):
        return self._scalar


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _DBStub:
    """``_get_session_or_404`` 第一次 execute() 返回 session，第二次返回事件列表。"""

    def __init__(self, *, session_obj: ChatSession | None, exec_events: list[ChatMessage]):
        self._session = session_obj
        self._exec_events = exec_events
        self._call_count = 0

    async def get(self, model, id_):  # noqa: ARG002
        if model is ChatSession:
            return self._session
        return None

    async def execute(self, _stmt):
        self._call_count += 1
        if self._call_count == 1:
            return _ResultStub(scalar=self._session)
        return _ResultStub(items=self._exec_events)


def _make_user(*, sid: uuid.UUID | None = None, is_superuser: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=sid or uuid.uuid4(), is_superuser=is_superuser)


def _make_event_msg(
    *, session_id: uuid.UUID, task_id: uuid.UUID, content: str = "✅ 已完成",
) -> ChatMessage:
    return ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="assistant",
        content=content,
        kind="execution_event",
        meta_data={"task_id": str(task_id), "result": {"status": "completed"}},
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_pending_summary_returns_recent_execution_events() -> None:
    user_id = uuid.uuid4()
    sid = uuid.uuid4()
    session = ChatSession(id=sid, user_id=user_id, project_id=uuid.uuid4())
    user = _make_user(sid=user_id)

    events = [
        _make_event_msg(session_id=sid, task_id=uuid.uuid4(), content="✅ A 完成"),
        _make_event_msg(session_id=sid, task_id=uuid.uuid4(), content="❌ B 失败"),
    ]
    db = _DBStub(session_obj=session, exec_events=events)

    summary = await chat_service.get_pending_task_summary(db, sid, user)  # type: ignore[arg-type]

    assert summary["session_id"] == str(sid)
    assert summary["count"] == 2
    items = summary["items"]
    assert len(items) == 2
    for item in items:
        assert "message_id" in item
        assert "task_id" in item
        assert "result" in item
        assert "content" in item
        assert "created_at" in item


@pytest.mark.asyncio
async def test_pending_summary_session_not_found() -> None:
    user = _make_user()
    db = _DBStub(session_obj=None, exec_events=[])
    with pytest.raises(NotFoundException):
        await chat_service.get_pending_task_summary(db, uuid.uuid4(), user)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pending_summary_other_user_denied() -> None:
    sid = uuid.uuid4()
    owner_id = uuid.uuid4()
    intruder = _make_user(sid=uuid.uuid4(), is_superuser=False)
    session = ChatSession(id=sid, user_id=owner_id, project_id=uuid.uuid4())
    db = _DBStub(session_obj=session, exec_events=[])
    with pytest.raises(PermissionDeniedException):
        await chat_service.get_pending_task_summary(db, sid, intruder)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pending_summary_empty_when_no_events() -> None:
    user_id = uuid.uuid4()
    sid = uuid.uuid4()
    session = ChatSession(id=sid, user_id=user_id, project_id=uuid.uuid4())
    user = _make_user(sid=user_id)
    db = _DBStub(session_obj=session, exec_events=[])
    summary = await chat_service.get_pending_task_summary(db, sid, user)  # type: ignore[arg-type]
    assert summary["count"] == 0
    assert summary["items"] == []
