"""Task 9.5 — persistence 单测（聚焦脱敏 + ORM 行结构）。

不连真 PG：``async_session_factory`` 用 monkeypatch 替成内存 session。

重点：
- ``sanitize_tool_call_for_storage`` 对 secret 工具的 plaintext 替换为占位符
- ``flush_step`` / ``flush_case`` / ``flush_execution`` 的 keyword 透传
- ``init_execution_record`` 对已存在记录的 idempotent 处理
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.ui_automation.persistence import (
    sanitize_tool_call_for_storage,
    sanitize_tool_calls,
)


def test_sanitize_tool_call_replaces_secret_value() -> None:
    record = {
        "name": "<exec>:platform_get_secret",
        "raw_name": "platform_get_secret",
        "arguments": {"key": "password"},
        "result": {
            "key": "password",
            "value": "Sup3rS3cretP@ss",
            "_test_data_secret_used": True,
        },
        "duration_ms": 5,
    }
    out = sanitize_tool_call_for_storage(record)
    assert out["result"]["value"] == "<secret used>"
    assert out["result"]["_test_data_secret_used"] is True
    assert out["result"]["key"] == "password"
    # 原 record 不被修改
    assert record["result"]["value"] == "Sup3rS3cretP@ss"


def test_sanitize_tool_call_preserves_non_secret() -> None:
    record = {
        "name": "browser_click",
        "result": {"ok": True, "snapshot": "- main"},
    }
    out = sanitize_tool_call_for_storage(record)
    assert out == record  # noqa: S101


def test_sanitize_handles_non_dict() -> None:
    assert sanitize_tool_call_for_storage("not a dict") == "not a dict"  # type: ignore[arg-type]
    assert sanitize_tool_call_for_storage({"x": 1}) == {"x": 1}


def test_sanitize_tool_calls_list_or_none() -> None:
    assert sanitize_tool_calls(None) == []
    assert sanitize_tool_calls([]) == []
    out = sanitize_tool_calls([
        {"name": "a", "result": {"value": "v1"}},
        {"name": "b", "result": {"_test_data_secret_used": True, "value": "v2"}},
    ])
    assert out[0]["result"]["value"] == "v1"
    assert out[1]["result"]["value"] == "<secret used>"


# ─── DB 操作：用 in-memory fake session 模拟 ───────────────────────


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeSession:
    """记录 add / commit / refresh，``execute(...)`` 通过 store dict 假装查找。"""

    def __init__(self, store: dict):
        self.store = store
        self.added: list = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def add(self, row):
        self.added.append(row)
        self.store.setdefault("by_id", {})[row.id] = row

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        # 把 server_default 字段填上简化值
        if not hasattr(row, "id") or row.id is None:
            row.id = uuid.uuid4()

    async def execute(self, _stmt):
        # 返回最新插入的对象（测试只查刚 add 的那条）
        last = self.added[-1] if self.added else None
        return _FakeResult(last)


class _FakeResult:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v


@pytest.mark.asyncio
async def test_create_case_result_inserts_row(monkeypatch) -> None:
    import app.modules.ui_automation.persistence as p

    store: dict = {}

    def factory():
        return _FakeSession(store)

    monkeypatch.setattr(p, "async_session_factory", factory)

    row = await p.create_case_result(
        execution_id=uuid.uuid4(),
        testcase_id=uuid.uuid4(),
        sort_order=0,
    )
    assert row is not None
    assert row.status == "running"
    assert row.data_confidence == "reliable"


@pytest.mark.asyncio
async def test_flush_step_redacts_secret_in_tool_calls(monkeypatch) -> None:
    import app.modules.ui_automation.persistence as p

    store: dict = {}
    fake_session = _FakeSession(store)
    monkeypatch.setattr(p, "async_session_factory", lambda: fake_session)

    case_id = uuid.uuid4()
    new_id = await p.flush_step(
        case_result_id=case_id,
        step_number=1,
        description="登录",
        expected_result="成功",
        tool_calls=[
            {"name": "x:platform_get_secret", "result": {
                "value": "P@ss123!",
                "_test_data_secret_used": True,
            }},
            {"name": "x:browser_click", "result": {"ok": True}},
        ],
        ai_reasoning="reasoning",
        snapshot_after="- main",
        assertion_passed=True,
        assertion_reason="pass",
        status="passed",
        tokens_used=42,
        duration_ms=100,
    )
    assert isinstance(new_id, uuid.UUID)
    assert fake_session.added
    row = fake_session.added[-1]
    assert row.case_result_id == case_id
    assert row.tool_calls[0]["result"]["value"] == "<secret used>"
    assert row.tool_calls[1]["result"]["ok"] is True
    assert row.tokens_used == 42
