"""Phase 13 / Task 13.1 — ``platform_run_ui_execution`` 永久屏蔽给 LLM。

DoD §11.5：LLM 试图调用 ``run_ui_test``（实际名 ``platform_run_ui_execution``）
时返回 ``Tool not allowed for AI invocation, must be confirmed by user``。
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.skills.safe_invoke import safe_run_tool
from app.modules.skills.skill_router import LLM_FORBIDDEN_PLATFORM_TOOLS


def test_forbidden_set_contains_run_ui_execution() -> None:
    assert "platform_run_ui_execution" in LLM_FORBIDDEN_PLATFORM_TOOLS


@pytest.mark.asyncio
async def test_safe_run_tool_blocks_run_ui_execution_even_when_allowed() -> None:
    """即使 caller 显式把 ``platform_run_ui_execution`` 放进 allowed_platform_tools
    且 system_ui_automation 已激活，仍要被黑名单拒绝（最严格的安全闸门）。"""
    raw = await safe_run_tool(
        AsyncMock(),
        "platform_run_ui_execution",
        "{}",
        active_system_skill_slugs={"system_ui_automation"},
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset({"platform_run_ui_execution"}),
        session_id=None,
        project_id=uuid.uuid4(),
    )
    payload = json.loads(raw)
    assert "error" in payload
    assert "Tool not allowed for AI invocation" in payload["error"]
    assert "must be confirmed by user" in payload["error"]


@pytest.mark.asyncio
async def test_safe_run_tool_blocks_run_ui_execution_when_no_active_skill() -> None:
    """没有激活任何 system_* skill 时也走黑名单分支（不会泄漏"哪条 tool 被禁"）。"""
    raw = await safe_run_tool(
        AsyncMock(),
        "platform_run_ui_execution",
        "{}",
        active_system_skill_slugs=set(),
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=None,
    )
    payload = json.loads(raw)
    assert "Tool not allowed for AI invocation" in payload["error"]


@pytest.mark.asyncio
async def test_system_ui_automation_tool_requires_active_skill() -> None:
    """``system__ui_automation__*`` tool 在无 skill 激活时也应拒（一致的命名空间闸门）。"""
    raw = await safe_run_tool(
        AsyncMock(),
        "system__ui_automation__search_test_cases",
        "{}",
        active_system_skill_slugs=set(),
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=None,
    )
    payload = json.loads(raw)
    assert "error" in payload
    assert "system_ui_automation skill" in payload["error"]


@pytest.mark.asyncio
async def test_system_ui_automation_tool_passes_when_skill_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``system__ui_automation__*`` tool 在 skill 激活时透传到 ``run_tool``。"""
    called: dict[str, str] = {}

    async def _fake_run_tool(name: str, args_json: str) -> str:
        called["name"] = name
        return '{"ok": true}'

    monkeypatch.setattr("app.modules.skills.safe_invoke.run_tool", _fake_run_tool)

    raw = await safe_run_tool(
        AsyncMock(),
        "system__ui_automation__search_test_cases",
        '{"query": "登录"}',
        active_system_skill_slugs={"system_ui_automation"},
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=None,
    )
    assert json.loads(raw) == {"ok": True}
    assert called["name"] == "system__ui_automation__search_test_cases"
