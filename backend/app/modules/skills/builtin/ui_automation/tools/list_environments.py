"""``system__ui_automation__list_environments`` 工具（Phase 13 / Task 13.2）。

返回项目内全部环境 + ``risk_level`` + 5 层优先级解析的 ``resolved_default``：
让 LLM 不必自己跑决策树，后端直接给一个推荐 environment_id。

5 层优先级（设计文档 §10.3.1）：
  1. 用户消息显式提到（"用 staging 跑"）
  2. 当前会话上下文绑定（task 13.3 用户上次 confirm 后写入）
  3. 项目级默认（M2 task 13.5 启用 ``Project.default_environment_id``）
  4. 用户上次执行过的环境（最近一次 ``ui_executions``）
  5. fallback：项目内首条 low risk 环境
  6. 全部缺失 → ``missing=True``，LLM 反问用户选环境
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.modules.skills.builtin.ui_automation.matchers.env_priority import (
    resolve_environment,
)
from app.modules.skills.builtin.ui_automation.plan_builder import _infer_risk_level
from app.modules.skills.builtin.ui_automation.schemas import (
    EnvironmentSummary,
    ListEnvironmentsResult,
    ResolvedEnvironmentDefault,
)
from app.modules.skills.platform_tools import _get_runtime
from app.modules.ui_automation.models import TestEnvironment

logger = logging.getLogger(__name__)


LIST_ENVIRONMENTS_TOOL_NAME = "system__ui_automation__list_environments"

LIST_ENVIRONMENTS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": LIST_ENVIRONMENTS_TOOL_NAME,
        "description": (
            "列出当前项目下可用的 UI 自动化执行环境（id / name / base_url / "
            "risk_level）+ 后端 5 层优先级解析得到的推荐默认环境 "
            "(``resolved_default``)。AI 应**优先采用** ``resolved_default."
            "environment_id`` 作为 propose_execution_plan 的 environment_id；"
            "仅当用户明确说'换到 xxx'时才覆盖。``resolved_default.missing=true`` "
            "时必须反问用户选环境，不能默认任何 high risk 环境。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 30",
                    "default": 30,
                },
            },
        },
    },
}


_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


async def exec_list_environments(args: dict[str, Any]) -> dict[str, Any]:
    rt = _get_runtime()
    if rt is None:
        return {
            "error": (
                "list_environments requires an active chat runtime "
                "(no project_id bound)"
            ),
        }

    limit = int(args.get("limit") or 30)
    limit = max(1, min(limit, 100))

    stmt = (
        select(TestEnvironment)
        .where(TestEnvironment.project_id == rt.project_id)
        .order_by(TestEnvironment.updated_at.desc())
        .limit(limit)
    )
    rows = list((await rt.db.execute(stmt)).scalars().all())

    summaries: list[EnvironmentSummary] = []
    for env in rows:
        level, reason = _infer_risk_level(env.name, str(env.base_url))
        summaries.append(
            EnvironmentSummary(
                id=env.id,
                name=env.name,
                base_url=str(env.base_url),
                risk_level=level,
                risk_reason=reason,
            ),
        )
    summaries.sort(
        key=lambda e: (_RISK_RANK.get(e.risk_level.value, 99), e.name.lower()),
    )

    # 把 environments 同步排序后给 env_priority——它的 fallback 层依赖"已按
    # risk 升序"假设。
    sorted_envs = sorted(
        rows,
        key=lambda env: (
            _RISK_RANK.get(_infer_risk_level(env.name, str(env.base_url))[0].value, 99),
            (env.name or "").lower(),
        ),
    )

    user_id = getattr(rt.user, "id", None) if rt.user is not None else None
    resolution = await resolve_environment(
        rt.db,
        project_id=rt.project_id,
        user_id=user_id,
        environments=sorted_envs,
        user_message=rt.user_message,
        session_chat_context=rt.chat_context,
    )
    resolved_default = ResolvedEnvironmentDefault(
        environment_id=resolution.environment_id,
        name=resolution.environment.name if resolution.environment else None,
        layer=resolution.layer.value,
        reason=resolution.reason,
        missing=resolution.missing,
    )

    payload = ListEnvironmentsResult(
        count=len(summaries),
        environments=summaries,
        resolved_default=resolved_default,
    )
    return payload.model_dump(mode="json")
