"""``system__ui_automation__list_test_data_sets`` 工具（Phase 13 / Task 13.1）。

M1：列项目下 ``test_data_sets``（按 scope 排序：environment → project →
personal）；返回 ``id / name / scope / item_count`` 等不含明文的摘要。

M2 task 13.4 给 ``test_data_sets`` 加 ``purpose / tags`` 后，返回结构会
扩展但向后兼容（schema 用了 BaseModel.model_dump，新增字段对 LLM
透明）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.modules.skills.builtin.ui_automation.schemas import (
    ListTestDataSetsResult,
    TestDataSetSummary,
)
from app.modules.skills.platform_tools import _get_runtime
from app.modules.test_data.models import TestDataSet

logger = logging.getLogger(__name__)


LIST_TEST_DATA_SETS_TOOL_NAME = "system__ui_automation__list_test_data_sets"

LIST_TEST_DATA_SETS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": LIST_TEST_DATA_SETS_TOOL_NAME,
        "description": (
            "列出当前项目下可用的测试物料集。返回 id / name / scope / "
            "is_default / item_count；不含敏感字段明文。"
            "AI 用来 (1) 在 propose_execution_plan 前确认默认物料集是否齐备 "
            "(2) 用户主动询问'有哪些账号集'时回答。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["project", "environment", "personal", "all"],
                    "description": "按 scope 过滤；默认 'all'",
                    "default": "all",
                },
                "environment_id": {
                    "type": "string",
                    "description": "scope=environment 时按此环境过滤；可省略",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 30",
                    "default": 30,
                },
            },
        },
    },
}


_SCOPE_RANK = {"environment": 0, "project": 1, "personal": 2}


async def exec_list_test_data_sets(args: dict[str, Any]) -> dict[str, Any]:
    import uuid as _uuid

    rt = _get_runtime()
    if rt is None:
        return {
            "error": (
                "list_test_data_sets requires an active chat runtime "
                "(no project_id bound)"
            ),
        }

    scope = (args.get("scope") or "all").strip().lower()
    if scope not in ("project", "environment", "personal", "all"):
        return {"error": f"invalid scope: {scope!r}"}

    env_id_raw = args.get("environment_id")
    env_id: _uuid.UUID | None = None
    if env_id_raw:
        try:
            env_id = _uuid.UUID(str(env_id_raw))
        except (TypeError, ValueError):
            return {"error": f"invalid environment_id: {env_id_raw!r}"}

    limit = int(args.get("limit") or 30)
    limit = max(1, min(limit, 100))

    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.project_id == rt.project_id)
    )
    if scope != "all":
        stmt = stmt.where(TestDataSet.scope == scope)
    if env_id is not None:
        stmt = stmt.where(TestDataSet.environment_id == env_id)
    stmt = stmt.order_by(TestDataSet.updated_at.desc()).limit(limit)

    rows = list((await rt.db.execute(stmt)).scalars().all())
    summaries = [
        TestDataSetSummary(
            id=s.id,
            name=s.name,
            description=s.description,
            category=s.category,
            scope=s.scope,
            environment_id=s.environment_id,
            is_default=bool(s.is_default),
            item_count=len(s.items or []),
        )
        for s in rows
    ]
    summaries.sort(
        key=lambda r: (_SCOPE_RANK.get(r.scope, 9), 0 if r.is_default else 1, r.name),
    )
    payload = ListTestDataSetsResult(
        count=len(summaries), test_data_sets=summaries,
    )
    return payload.model_dump(mode="json")
