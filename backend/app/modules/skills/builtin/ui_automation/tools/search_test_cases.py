"""``system__ui_automation__search_test_cases`` 工具（Phase 13 / Task 13.2）。

实现：调 ``matchers.case_matcher.match_test_cases`` 跑三策略级联——

- 策略 1：``#NNN`` / ``TC-NNN`` / UUID 精确（score=1.0）
- 策略 2：title 模糊 + tags GIN 召回（score 0.4 ~ 0.95）
- 策略 3：步骤内容 ilike 兜底（score 0.3 ~ 0.6）

返回 ``CaseSummary`` 列表，含 ``relevance_score`` 与 ``matched_via``——前端
ConfirmationCard 后续会基于这两个字段渲染"为什么命中"徽章 + 排序。
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.skills.builtin.ui_automation.matchers.case_matcher import (
    candidate_to_dict,
    match_test_cases,
)
from app.modules.skills.builtin.ui_automation.schemas import (
    CaseSummary,
    SearchTestCasesResult,
)
from app.modules.skills.platform_tools import _get_runtime

logger = logging.getLogger(__name__)


SEARCH_TEST_CASES_TOOL_NAME = "system__ui_automation__search_test_cases"

SEARCH_TEST_CASES_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SEARCH_TEST_CASES_TOOL_NAME,
        "description": (
            "在当前项目下搜索 UI 自动化测试用例（三策略级联：① #NNN/TC-NNN/UUID "
            "精确；② title + tags 模糊；③ 步骤内容召回）。每条返回含 "
            "relevance_score (0..1) 与 matched_via 命中策略，AI 据此判断："
            "命中 1 条 → 直接传给 propose_execution_plan；命中 N 条 → 让用户选；"
            "命中 0 条 → 走 adhoc 流程（M2 task 13.6 接通）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "搜索关键字。支持自然语言（'登录用例'/'回归用例'/"
                        "'点击登录按钮'）、编号引用（'#123' / 'TC-0042'）、"
                        "用例 UUID 直填。可省略（返回最近更新的若干条）。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，默认 10，上限 30",
                    "default": 10,
                },
            },
        },
    },
}


async def exec_search_test_cases(args: dict[str, Any]) -> dict[str, Any]:
    rt = _get_runtime()
    if rt is None:
        return {
            "error": (
                "search_test_cases requires an active chat runtime "
                "(no project_id bound)"
            ),
        }

    raw_q = args.get("query")
    q = (raw_q or "").strip() if isinstance(raw_q, str) else ""
    limit = int(args.get("limit") or 10)
    limit = max(1, min(limit, 30))

    candidates = await match_test_cases(
        rt.db, q, rt.project_id, limit=limit,
    )

    cases = [
        CaseSummary(
            id=c.case.id,
            case_no=c.case.case_no,
            title=c.case.title,
            priority=c.case.priority,
            status=c.case.status,
            relevance_score=round(float(c.relevance_score), 3),
            matched_via=[m.value for m in c.matched_via],
        )
        for c in candidates
    ]
    payload = SearchTestCasesResult(
        count=len(cases),
        cases=cases,
        query=q[:100] if q else None,
    )
    out = payload.model_dump(mode="json")
    # 调试日志：让排错时一眼看到三策略的命中分布
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "search_test_cases: q=%r → %d candidates: %s",
            q, len(candidates),
            [candidate_to_dict(c) for c in candidates],
        )
    return out
