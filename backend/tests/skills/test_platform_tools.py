"""Task 12.4 — platform_tools 运行时门禁 + safe_invoke 第二道闸。"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.skills.platform_tools import (
    _exec_list_environments,
    _exec_run_ui_execution,
    _exec_search_testcases,
    chat_platform_runtime_cm,
    ensure_platform_tools_registered,
    platform_chat_openai_schemas,
)
from app.modules.skills.safe_invoke import safe_run_tool


@pytest.mark.asyncio
async def test_platform_handlers_need_runtime() -> None:
    r1 = await _exec_search_testcases({})
    r2 = await _exec_list_environments({})
    r3 = await _exec_run_ui_execution({"testcase_ids": []})
    assert "error" in r1
    assert "error" in r2
    assert "error" in r3


def test_platform_chat_openai_schemas_complete() -> None:
    schemas = platform_chat_openai_schemas()
    assert set(schemas.keys()) == {
        "platform_search_testcases",
        "platform_list_environments",
        "platform_run_ui_execution",
    }


def test_ensure_platform_tools_registered_idempotent() -> None:
    ensure_platform_tools_registered()
    ensure_platform_tools_registered()
    from app.modules.llm import agent_tools

    assert "platform_search_testcases" in agent_tools.TOOL_REGISTRY
    assert "platform_list_environments" in agent_tools.TOOL_REGISTRY
    assert "platform_run_ui_execution" in agent_tools.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_safe_run_tool_rejects_platform_when_no_active_system_skill() -> None:
    """第二道闸：对话上下文里没有任何 system_* skill → platform_* 一律拒。"""
    raw = await safe_run_tool(
        AsyncMock(),
        "platform_search_testcases",
        "{}",
        active_system_skill_slugs=set(),
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=None,
    )
    payload = json.loads(raw)
    assert "error" in payload
    assert "active system_* skill" in payload["error"]


@pytest.mark.asyncio
async def test_safe_run_tool_rejects_platform_not_declared_by_skill() -> None:
    """system_* skill 已激活，但该 platform_* 未在其 tools_required 中 → 拒。

    Phase 13 / Task 13.1：``platform_run_ui_execution`` 已放进 LLM 黑名单走更
    严格的拒绝分支（"Tool not allowed for AI invocation"）；本 test 改用
    ``platform_list_environments`` 验证"未声明"分支仍按原逻辑拒绝。
    """
    raw = await safe_run_tool(
        AsyncMock(),
        "platform_list_environments",
        "{}",
        active_system_skill_slugs={"system_ui_automation"},
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset({"platform_search_testcases"}),
        session_id=None,
        project_id=None,
    )
    payload = json.loads(raw)
    assert "error" in payload
    assert "not declared" in payload["error"]


@pytest.mark.asyncio
async def test_safe_run_tool_allows_platform_when_active_and_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """system_* skill 已激活 + tool 在 allowed_platform_tools → 透传到 run_tool。"""
    called: dict[str, str] = {}

    async def _fake_run_tool(name: str, args_json: str) -> str:
        called["name"] = name
        called["args"] = args_json
        return json.dumps({"ok": True})

    monkeypatch.setattr("app.modules.skills.safe_invoke.run_tool", _fake_run_tool)

    raw = await safe_run_tool(
        AsyncMock(),
        "platform_list_environments",
        "{}",
        active_system_skill_slugs={"system_ui_automation"},
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset({"platform_list_environments"}),
        session_id=None,
        project_id=None,
    )
    payload = json.loads(raw)
    assert payload == {"ok": True}
    assert called["name"] == "platform_list_environments"


@pytest.mark.asyncio
async def test_chat_platform_runtime_cm_no_op_when_project_id_none() -> None:
    cm = chat_platform_runtime_cm(AsyncMock(), MagicMock(), None, None, None)
    assert hasattr(cm, "__aenter__")
    async with cm:
        result = await _exec_search_testcases({})
        assert "error" in result


@pytest.mark.asyncio
async def test_chat_platform_runtime_cm_sets_runtime() -> None:
    """挂载 runtime 后 platform_* handler 不应再因 runtime 缺失而报错。"""
    db = AsyncMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=[])
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=exec_result)

    user = MagicMock()
    pid = uuid.uuid4()

    async with chat_platform_runtime_cm(db, user, pid, None, None):
        out = await _exec_list_environments({})
        assert out == {"count": 0, "environments": []}
