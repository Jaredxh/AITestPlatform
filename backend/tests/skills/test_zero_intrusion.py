"""Task 12.2 — SkillContext 空对象时 chat 组装零侵入。"""

from __future__ import annotations

import uuid

from app.modules.llm.agent_tools import TOOLS
from app.modules.llm.chat_service import (
    merge_skill_context_into_openai_messages,
    tools_for_chat_session,
)
from app.modules.llm.models import ChatSession
from app.modules.skills.skill_router import SkillContext


def test_empty_skill_context_preserves_messages_and_tools_identity() -> None:
    session = ChatSession(
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        system_prompt="自定义",
    )
    user_content = "你好"

    from app.modules.llm.chat_service import _build_context

    base = _build_context(session, user_content)
    ctx = SkillContext()

    merged = merge_skill_context_into_openai_messages(base, ctx)
    assert merged is base

    assert tools_for_chat_session(ctx) is TOOLS
