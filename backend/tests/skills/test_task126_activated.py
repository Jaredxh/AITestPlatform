"""Task 12.6 — compose 暴露 activated_skills + execute_skill_invoke 写回 message。"""

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
async def test_activated_includes_always_manual_trigger_not_agent_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj = uuid.uuid4()
    always_s = _skill(slug="system_always", activation_mode="always", name="A")
    manual_s = _skill(slug="manual_one", activation_mode="manual", name="M")
    trig_s = _skill(
        slug="trig_one",
        activation_mode="trigger",
        triggers=["跑用例"],
        name="T",
    )
    pool_s = _skill(
        slug="pool_one",
        activation_mode="agent_callable",
        name="P",
    )

    monkeypatch.setattr(
        skill_router,
        "_list_always_skills",
        AsyncMock(return_value=[always_s]),
    )
    monkeypatch.setattr(
        skill_router,
        "_fetch_skills_by_ids",
        AsyncMock(return_value=[manual_s]),
    )
    monkeypatch.setattr(
        skill_router,
        "match_triggers",
        AsyncMock(return_value=[trig_s]),
    )
    monkeypatch.setattr(
        skill_router,
        "_list_agent_callable",
        AsyncMock(return_value=[pool_s]),
    )

    sess = ChatSession(project_id=proj, user_id=uuid.uuid4())
    sess.chat_context = {"manual_skill_ids": [str(manual_s.id)]}

    ctx = await skill_router.compose(AsyncMock(), proj, sess, "帮我跑用例")

    activated_slugs = {a.slug for a in ctx.activated_skills}
    reasons_by_slug = {a.slug: a.activation_reason for a in ctx.activated_skills}
    assert activated_slugs == {"system_always", "manual_one", "trig_one"}
    assert reasons_by_slug["system_always"] == "always"
    assert reasons_by_slug["manual_one"] == "manual"
    assert reasons_by_slug["trig_one"] == "trigger_match"

    # agent_callable pool 候选 NOT in activated（被动可调，不算"已激活"）
    assert "pool_one" not in activated_slugs

    # trigger 命中要回填 matched_trigger 文案
    trig_info = next(a for a in ctx.activated_skills if a.slug == "trig_one")
    assert trig_info.matched_trigger == "跑用例"


@pytest.mark.asyncio
async def test_execute_skill_invoke_writes_back_skill_invocation_id() -> None:
    """模型调 ``skill_*__invoke`` 后，应把 SkillUsageLog.id 回写到该 ChatMessage。"""
    from app.modules.llm.models import ChatMessage
    from app.modules.skills.models import SkillUsageLog
    from app.modules.skills.skill_router import execute_skill_invoke

    skill = _skill(slug="custom_x", db_version=2)
    fake_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role="assistant",
        content="",
    )
    log_id_holder: dict[str, uuid.UUID] = {}

    def _fake_add(obj: object) -> None:
        # 真实 SQLAlchemy 在 flush 才赋 PK；mock 这里直接补一个 uuid 模拟 flush 行为
        if isinstance(obj, SkillUsageLog):
            obj.id = uuid.uuid4()
            log_id_holder["log_id"] = obj.id

    db = AsyncMock()
    db.add = _fake_add
    db.flush = AsyncMock()

    exec_result = AsyncMock()
    exec_result.scalar_one_or_none = lambda: skill
    db.execute = AsyncMock(return_value=exec_result)
    db.get = AsyncMock(return_value=fake_msg)

    out = await execute_skill_invoke(
        db,
        skill.id,
        "{}",
        session_id=uuid.uuid4(),
        project_id=skill.project_id,
        assistant_message_id=fake_msg.id,
    )
    assert "instructions_markdown" in out
    assert fake_msg.skill_invocation_id == log_id_holder["log_id"]


@pytest.mark.asyncio
async def test_execute_skill_invoke_commits_short_transaction() -> None:
    """行锁回归：``execute_skill_invoke`` 必须在写完 SkillUsageLog + 回写 message
    元数据后立刻 commit，否则主 db 会一直占着 ``chat_messages`` 行锁，
    后台 ``persist()`` 用独立 session 想 UPDATE 同一行就会卡死，整个 chat
    任务表现为"输出几个字就再也不动了 + 重发也不响应"。
    """
    from app.modules.llm.models import ChatMessage
    from app.modules.skills.models import SkillUsageLog
    from app.modules.skills.skill_router import execute_skill_invoke

    skill = _skill(slug="commit_x")
    fake_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role="assistant",
        content="",
    )

    def _fake_add(obj: object) -> None:
        if isinstance(obj, SkillUsageLog):
            obj.id = uuid.uuid4()

    db = AsyncMock()
    db.add = _fake_add
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    exec_result = AsyncMock()
    exec_result.scalar_one_or_none = lambda: skill
    db.execute = AsyncMock(return_value=exec_result)
    db.get = AsyncMock(return_value=fake_msg)

    await execute_skill_invoke(
        db,
        skill.id,
        "{}",
        session_id=uuid.uuid4(),
        project_id=skill.project_id,
        assistant_message_id=fake_msg.id,
    )

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_skill_invoke_skill_not_found_does_not_commit() -> None:
    """skill 不存在时直接早返回，不应启动一个空 commit 浪费连接。"""
    from app.modules.skills.skill_router import execute_skill_invoke

    db = AsyncMock()
    db.commit = AsyncMock()
    exec_result = AsyncMock()
    exec_result.scalar_one_or_none = lambda: None
    db.execute = AsyncMock(return_value=exec_result)

    out = await execute_skill_invoke(
        db,
        uuid.uuid4(),
        "{}",
        session_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
    )
    assert "skill not found" in out
    db.commit.assert_not_awaited()
