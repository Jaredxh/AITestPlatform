"""Phase 13 / Task 13.0 — SkillRouter.compose 二段式 NLU 校验单测。

DoD：``query_history / learn / edit_testcase / other`` 意图下，
``system_ui_automation`` 候选**必须**从 ``candidate_tools`` 与
``activated_skills`` 同步剔除（不让 LLM 看到"诱惑"）。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.llm.models import ChatSession
from app.modules.skills import skill_router
from app.modules.skills.builtin.ui_automation import intent_classifier
from app.modules.skills.models import Skill


@pytest.fixture(autouse=True)
def _isolate_intent_cache() -> None:
    intent_classifier._cache_clear_for_test()


def _ui_skill() -> Skill:
    return Skill(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="UI 自动化",
        slug="system_ui_automation",
        description="UI 自动化执行",
        body="# UI 自动化\n\n## 何时使用\n执行 UI 用例。\n",
        triggers=["跑用例", "执行用例"],
        tools_required=["platform_run_ui_execution"],
        activation_mode="trigger",
        created_by=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_query_history_drops_ui_automation_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj = uuid.uuid4()
    ui = _ui_skill()
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[ui]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "昨天跑用例的失败率多少",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_system_ui_automation__invoke" not in names
    assert "platform_run_ui_execution" not in names
    assert "system_ui_automation" not in ctx.active_system_skill_slugs
    assert all(a.slug != "system_ui_automation" for a in ctx.activated_skills)


@pytest.mark.asyncio
async def test_learn_intent_drops_ui_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    ui = _ui_skill()
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[ui]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(), proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "怎么写好登录用例",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_system_ui_automation__invoke" not in names


@pytest.mark.asyncio
async def test_execute_test_keeps_ui_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    proj = uuid.uuid4()
    ui = _ui_skill()
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[ui]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(), proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "跑下登录用例",
    )
    names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_system_ui_automation__invoke" in names
    assert "system_ui_automation" in ctx.active_system_skill_slugs


@pytest.mark.asyncio
async def test_always_layer_ui_automation_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """always 层主动激活的 system_ui_automation 不应被 NLU 剔除（用户明确选择优先）。

    Phase 13 / Task 13.1：即使 SKILL.md 历史 ``tools_required`` 仍写着
    ``platform_run_ui_execution``，也**不会**进入 ``allowed_platform_tools``
    或 ``candidate_tools``（被 ``LLM_FORBIDDEN_PLATFORM_TOOLS`` 黑名单屏蔽）；
    取而代之的是 4 个 ``system__ui_automation__*`` 工具上场。
    """
    proj = uuid.uuid4()
    ui = _ui_skill()
    ui.activation_mode = "always"
    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[ui]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(), proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "今天上海天气",
    )
    # always 层添加的 system_ skill：active_slugs 保留
    assert "system_ui_automation" in ctx.active_system_skill_slugs

    # platform_run_ui_execution 永远屏蔽：不在 candidate_tools，也不在 allowed_platform_tools
    cand_names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "platform_run_ui_execution" not in cand_names
    assert "platform_run_ui_execution" not in ctx.allowed_platform_tools

    # 4 个 system__ui_automation__* tool 全部到位
    assert "system__ui_automation__search_test_cases" in cand_names
    assert "system__ui_automation__list_environments" in cand_names
    assert "system__ui_automation__list_test_data_sets" in cand_names
    assert "system__ui_automation__propose_execution_plan" in cand_names
