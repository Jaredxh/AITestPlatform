"""Task 12.2 — SkillRouter compose 行为（mock DB 依赖函数）。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.llm.models import ChatSession
from app.modules.skills import skill_router
from app.modules.skills.models import Skill


def _skill(**kw: object) -> Skill:
    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="Skill",
        slug="custom_slug",
        description="desc",
        body="# T\n\n## 何时使用\nWhen.\n",
        created_by=uuid.uuid4(),
        triggers=[],
        tools_required=[],
        activation_mode="agent_callable",
    )
    base.update(kw)
    return Skill(**base)


@pytest.mark.asyncio
async def test_trigger_and_invoke_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    trig = _skill(
        slug="trig_skill",
        activation_mode="trigger",
        triggers=["UI 测试"],
    )
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        skill_router,
        "match_triggers",
        AsyncMock(return_value=[trig]),
    )
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "帮我跑一下 UI 测试",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_trig_skill__invoke" in names


@pytest.mark.asyncio
async def test_system_skill_merges_platform_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    sys_s = _skill(
        slug="system_ui_automation",
        tools_required=["platform_run_ui_execution", "platform_search_testcases"],
        activation_mode="agent_callable",
    )
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        skill_router,
        "_list_agent_callable",
        AsyncMock(return_value=[sys_s]),
    )

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "hi",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_system_ui_automation__invoke" in names
    assert "platform_run_ui_execution" in names
    assert "platform_search_testcases" in names
    assert "system_ui_automation" in ctx.active_system_skill_slugs
    assert "platform_run_ui_execution" in ctx.allowed_platform_tools


@pytest.mark.asyncio
async def test_custom_skill_does_not_expose_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    custom = _skill(
        slug="my_custom",
        tools_required=["platform_run_ui_execution"],
        activation_mode="agent_callable",
    )
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        skill_router,
        "_list_agent_callable",
        AsyncMock(return_value=[custom]),
    )

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "x",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_my_custom__invoke" in names
    assert "platform_run_ui_execution" not in names
    assert ctx.allowed_platform_tools == frozenset()


@pytest.mark.asyncio
async def test_always_system_skill_adds_active_and_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    always_s = _skill(
        slug="system_always_x",
        activation_mode="always",
        tools_required=["platform_list_environments"],
        body="# B",
    )
    monkeypatch.setattr(
        skill_router,
        "_list_always_skills",
        AsyncMock(return_value=[always_s]),
    )
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "yo",
    )
    assert "system_always_x" in ctx.active_system_skill_slugs
    assert "platform_list_environments" in ctx.allowed_platform_tools
    assert any(
        t["function"]["name"] == "platform_list_environments"
        for t in ctx.candidate_tools
    )


@pytest.mark.asyncio
async def test_compose_empty_without_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock())
    ctx = await skill_router.compose(
        AsyncMock(),
        None,
        ChatSession(project_id=None, user_id=uuid.uuid4()),
        "hi",
    )
    assert ctx.candidate_tools == []
    skill_router._list_always_skills.assert_not_called()


@pytest.mark.asyncio
async def test_compose_exposes_http_tools_when_skill_has_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 12 优化：candidate skill body 含 http(s) URL → 自动暴露 http_get/post 工具。"""
    proj = uuid.uuid4()
    custom = _skill(
        slug="cq-qa-financial-reportcheck",
        triggers=["长轻订单底表检查"],
        body=(
            "# 长轻订单数据查询\n\n"
            "## 何时使用\n用户问订单底表更新情况时。\n\n"
            "## 接口\n"
            "GET http://172.17.208.45:5004/api/platform-updates/all\n"
            "GET http://172.17.208.45:5006/api/platform-updates/all?month=2026-03\n"
        ),
        activation_mode="trigger",
    )
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[custom]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "长轻订单底表检查",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_cq-qa-financial-reportcheck__invoke" in names
    assert "http_get_json" in names
    assert "http_post_json" in names
    assert ctx.allowed_http_hosts == frozenset(
        {"172.17.208.45:5004", "172.17.208.45:5006"},
    )


@pytest.mark.asyncio
async def test_compose_no_http_tools_when_skill_has_no_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没 URL 的 skill 不暴露 http_*；保持现有 chat 字节级等价。"""
    proj = uuid.uuid4()
    plain = _skill(slug="plain_skill", triggers=["x"], body="# 纯文本指南，无任何 URL。")
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[plain]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "x",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "http_get_json" not in names
    assert "http_post_json" not in names
    assert ctx.allowed_http_hosts == frozenset()
