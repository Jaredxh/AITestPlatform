"""Skill / platform_* / http_* tool 调用第二道闸（Phase 12 / Task 12.2 优化）。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.agent_tools import run_tool
from app.modules.skills.http_tools import (
    is_http_tool,
    reset_active_allowed_hosts,
    run_http_tool,
    set_active_allowed_hosts,
)
from app.modules.skills.skill_router import execute_skill_invoke


async def safe_run_tool(
    db: AsyncSession,
    name: str,
    args_json: str,
    *,
    active_system_skill_slugs: set[str],
    skill_id_by_tool_name: dict[str, uuid.UUID],
    allowed_platform_tools: frozenset[str],
    session_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    assistant_message_id: uuid.UUID | None = None,
    allowed_http_hosts: frozenset[str] = frozenset(),
) -> str:
    """包装 ``run_tool``：校验 ``platform_*``；派发 ``skill_*__invoke``、``http_*``。

    ``assistant_message_id`` 透传给 ``execute_skill_invoke`` 用于回写
    ``ChatMessage.skill_invocation_id``——前端徽章定位的关键。

    ``allowed_http_hosts`` 来自 ``SkillContext.allowed_http_hosts``——本轮所有
    candidate skill 的 SKILL.md 正文中明文出现过的 ``host:port``；进入
    ``run_http_tool`` 之前临时设置到 ContextVar，结束时复位。
    """
    if name.startswith("platform_"):
        if not active_system_skill_slugs:
            return json.dumps(
                {
                    "error": (
                        "platform_* tools require an active system_* skill "
                        "routed in this conversation turn."
                    ),
                },
                ensure_ascii=False,
            )
        if name not in allowed_platform_tools:
            return json.dumps(
                {
                    "error": (
                        f"platform tool {name!r} is not declared by any active "
                        "system_* skill tools_required list."
                    ),
                },
                ensure_ascii=False,
            )
        return await run_tool(name, args_json)

    if is_http_tool(name):
        # 只暴露给"本轮存在 candidate skill 且至少有一条 http URL"的对话。
        # 没有白名单时直接拒绝——LLM 可能在没激活任何技能时凭空发起 http_get_json。
        if not allowed_http_hosts:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "http_* tools are only available when a skill with "
                        "http(s) URLs in its SKILL.md is activated this turn."
                    ),
                },
                ensure_ascii=False,
            )
        token = set_active_allowed_hosts(allowed_http_hosts)
        try:
            return await run_http_tool(name, args_json)
        finally:
            reset_active_allowed_hosts(token)

    if name.startswith("skill_") and name.endswith("__invoke"):
        sid = skill_id_by_tool_name.get(name)
        if sid is None:
            return json.dumps(
                {"error": "unknown skill invoke tool"},
                ensure_ascii=False,
            )
        return await execute_skill_invoke(
            db,
            sid,
            args_json,
            session_id=session_id,
            project_id=project_id,
            assistant_message_id=assistant_message_id,
        )

    return await run_tool(name, args_json)
