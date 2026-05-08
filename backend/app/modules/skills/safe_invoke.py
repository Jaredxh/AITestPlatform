"""Skill / platform_* / http_* / system__*__* tool 调用第二道闸（Phase 13 / Task 13.1）。

Phase 13 / Task 13.1：在 Phase 12 闸门基础上叠加：

1. ``platform_run_ui_execution`` 永远拒绝 LLM 调用（设计文档 §10.3.3 / §10.7
   "run_ui_test 不暴露给 LLM" 的最后一道闸门），统一返回标准错误信息：
   ``Tool not allowed for AI invocation, must be confirmed by user``。即使有
   人通过 prompt-injection 绕过 OpenAI tool list 让模型生成该 tool name，
   safe_run_tool 仍直接拒绝执行。
2. ``system__<slug>__<tool>``（如 ``system__ui_automation__search_test_cases``）
   只在该 skill slug 已激活时放行；与 ``platform_*`` 闸门并行而非互斥。
"""

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
from app.modules.skills.skill_router import (
    LLM_FORBIDDEN_PLATFORM_TOOLS,
    SYSTEM_UI_AUTOMATION_TOOL_NAMES,
    execute_skill_invoke,
)

# 设计文档 §10.3.3 标准拒绝信息；前端可据此渲染"该工具仅在用户点击 ConfirmationCard
# 上的'确认执行'按钮后由专门 API 派发"提示文案。
_FORBIDDEN_LLM_TOOL_ERROR = (
    "Tool not allowed for AI invocation, must be confirmed by user. "
    "Use system__ui_automation__propose_execution_plan to generate a "
    "ConfirmationCard instead."
)


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
    """包装 ``run_tool``：校验 ``platform_*``；派发 ``skill_*__invoke``、``http_*``、
    ``system__<slug>__<tool>``。

    ``assistant_message_id`` 透传给 ``execute_skill_invoke`` 用于回写
    ``ChatMessage.skill_invocation_id``——前端徽章定位的关键。

    ``allowed_http_hosts`` 来自 ``SkillContext.allowed_http_hosts``——本轮所有
    candidate skill 的 SKILL.md 正文中明文出现过的 ``host:port``；进入
    ``run_http_tool`` 之前临时设置到 ContextVar，结束时复位。
    """
    # ── 第 0 道闸：LLM 黑名单（Task 13.1）─────────────────────────
    # platform_run_ui_execution 等"直接派发执行" tool 永远拒；即使
    # active_system_skill_slugs 含 system_ui_automation 也拒——LLM 只能调
    # propose_execution_plan 走前端 confirm 路径。
    if name in LLM_FORBIDDEN_PLATFORM_TOOLS:
        return json.dumps(
            {"error": _FORBIDDEN_LLM_TOOL_ERROR},
            ensure_ascii=False,
        )

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

    # ── system__<slug>__<tool> 命名空间闸门（Task 13.1）────────────
    # 4 个 system__ui_automation__* tool 仅在 system_ui_automation slug 激活
    # 时可调；其它 system__*__* 命名空间预留给后续内置 skill。
    if name in SYSTEM_UI_AUTOMATION_TOOL_NAMES:
        if "system_ui_automation" not in active_system_skill_slugs:
            return json.dumps(
                {
                    "error": (
                        "system__ui_automation__* tools require the "
                        "system_ui_automation skill to be activated this turn."
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
