"""历史回放（Task 9.7）。

不启动浏览器、不调 LLM——把已经持久化的 ``ui_executions / ui_case_results /
ui_step_results`` 直接通过 SSE 协议吐出，让前端用同一套 ``useExecutionSSE``
组件就能"重看"过去任意一次执行的进度时间线。

设计要点：
1. **事件序列与 ExecutionEngine 实时跑时严格同构**：``execution_started →
   N × (case_started → M × (step_started → step_complete) → case_complete)
   → execution_complete``。前端用相同的状态机，无需感知"实时 vs 回放"。
2. **额外加 ``replay=true`` 标记**：所有事件 payload 里塞一个 boolean，
   方便前端在右上角放一个"⏮ 回放"角标，不要让用户以为是真在跑。
3. **媒体路径以 URL 形式给出**：原始 ``screenshot_path`` 是后端绝对路径，
   前端访问不到；这里转成 ``/api/ui-executions/{id}/screenshots/{step_id}.png``
   形式（接口在 Task 10.5 实现，本 task 只产 URL）。当前没拍 step 截图时
   就不带 ``screenshot_url`` 字段。
4. **节流可选**：``inter_step_delay_seconds`` 让前端选"按真时间轴回放"还是
   "瀑布式即刻全部出"——默认 0 = 全部即出，UI 自己负责动画化。

Secret 脱敏：persistence 层在写库时已经把 ``platform_get_secret`` 等的
plaintext 替成 ``"<secret used>"`` 了；replayer 直接读 DB 不会再泄露。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundException
from app.database import async_session_factory
from app.modules.ui_automation.models import UICaseResult, UIExecution
from app.modules.ui_automation.sse import sse_done, sse_encode

logger = logging.getLogger(__name__)


# ─── 主入口 ──────────────────────────────────────────────────────────


async def replay(
    execution_id: uuid.UUID,
    *,
    inter_step_delay_seconds: float = 0.0,
    inter_case_delay_seconds: float = 0.0,
) -> AsyncGenerator[str, None]:
    """从 DB 读 execution 全树，按时间轴 yield SSE 帧。

    异常：
    - execution 不存在 → 抛 ``NotFoundException``（router 转 404）
    - case_results / step_results 为空 → 仍发 ``execution_started`` +
      ``execution_complete``，前端会显示"该执行没有任何用例数据"
    """
    row, testcase_meta = await _load_execution(execution_id)

    yield sse_encode({
        "type": "execution_started",
        "execution_id": str(row.id),
        "total_cases": row.total_cases,
        "mode": row.mode,
        "replay": True,
    })

    # 一些"环境/物料"事件不在 step 表里，但在执行时是被发过的。能回放的最有
    # 价值的是 ``test_data_snapshot``：让用户看到"当时跑的时候用的是哪些物料"
    # 以便复现 bug。
    if row.test_data_snapshot is not None:
        yield sse_encode({
            "type": "data_snapshot",
            "execution_id": str(row.id),
            "snapshot": row.test_data_snapshot,
            "replay": True,
        })

    case_results = sorted(
        list(row.case_results or []),
        key=lambda c: (c.sort_order, c.created_at),
    )

    for case in case_results:
        # 用例的人类可读标识（编号 + 标题 + 模块）不在 ``ui_case_results`` 表
        # 里——它们存在 ``testcases`` / ``testcase_modules`` 上。Task 11 修复：
        # replay SSE 必须带这些字段，否则前端只能显示 ``case_result_id`` 前 8
        # 位 hash，用户分不清是哪条用例（实际故障：用户看到 "用例 24835e6d"
        # 而不是 "TC-0061 创作者ID查询"）。
        meta = testcase_meta.get(case.testcase_id) if case.testcase_id else None
        yield sse_encode({
            "type": "case_started",
            "case_result_id": str(case.id),
            "testcase_id": str(case.testcase_id) if case.testcase_id else None,
            "sort_order": case.sort_order,
            "title": (meta or {}).get("title") or "",
            "testcase_no": (meta or {}).get("case_no"),
            "testcase_module_name": (meta or {}).get("module_name"),
            "replay": True,
        })

        steps = sorted(
            list(case.step_results or []),
            key=lambda s: s.step_number,
        )
        for step in steps:
            yield sse_encode({
                "type": "step_started",
                "case_result_id": str(case.id),
                "step_number": step.step_number,
                "action_preview": (step.description or "")[:200],
                "replay": True,
            })

            if inter_step_delay_seconds > 0:
                await asyncio.sleep(inter_step_delay_seconds)

            yield sse_encode(_step_complete_payload(case.id, step))

        yield sse_encode({
            "type": "case_complete",
            "case_result_id": str(case.id),
            "testcase_id": str(case.testcase_id) if case.testcase_id else None,
            "status": case.status,
            "data_confidence": case.data_confidence,
            "duration_ms": case.duration_ms,
            "tokens_used": case.tokens_used,
            "error_message": case.error_message,
            "synthesized_data": list(case.synthesized_data or []),
            "data_failures": list(case.data_failures or []),
            "test_data_used": list(case.test_data_used or []),
            "replay": True,
        })

        if inter_case_delay_seconds > 0:
            await asyncio.sleep(inter_case_delay_seconds)

    yield sse_encode({
        "type": "execution_complete",
        "execution_id": str(row.id),
        "status": row.status,
        "passed": row.passed_cases,
        "failed": row.failed_cases,
        "skipped": row.skipped_cases,
        "duration_ms": row.duration_ms,
        "tokens_total": row.tokens_total,
        "error_message": row.error_message,
        "replay": True,
    })

    yield sse_done({"execution_id": str(row.id), "replay": True})


# ─── helpers ─────────────────────────────────────────────────────────


async def _load_execution(
    execution_id: uuid.UUID,
) -> tuple[UIExecution, dict[uuid.UUID, dict[str, Any]]]:
    """eager-load execution + 全部 cases + 全部 steps，并附带 testcase 元数据。

    用 nested ``selectinload`` 一次拉齐——回放是"读密集 + 单次输出"场景，
    分批查反而 N+1。

    返回 ``(row, testcase_meta)``：
    * ``row`` —— ``UIExecution`` 及其全部 case / step 已经 eager-loaded
    * ``testcase_meta`` —— ``{testcase_id → {case_no, title, module_name, ...}}``
      给 SSE 事件渲染人类可读标识用（``TC-0061 标题``）。
    """
    async with async_session_factory() as session:
        stmt = (
            select(UIExecution)
            .options(
                selectinload(UIExecution.case_results).selectinload(
                    UICaseResult.step_results,
                ),
            )
            .where(UIExecution.id == execution_id)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundException("执行记录不存在")
        # 同步加载 testcase 元数据（编号 / 标题 / 模块名），让 case_started SSE
        # 事件能携带 ``TC-XXXX`` 形式的人类可读标识。复用 execution_service 的
        # 批量查询，避免 replay 路径 N+1。session 关闭前完成。
        from app.modules.ui_automation.execution_service import (
            _load_testcase_meta_for_cases,
        )
        testcase_meta = await _load_testcase_meta_for_cases(
            session, list(row.case_results or []),
        )
        # detach 后给调用方 —— 关 session 后还能继续读已 eager-loaded 字段
        return row, testcase_meta


def _step_complete_payload(case_id: uuid.UUID, step: Any) -> dict[str, Any]:
    """复刻 ``ExecutionEngine`` 实时跑时的 step_complete 事件结构。

    与实时结构差一个字段：``screenshot_url``——只在持久化路径存在时才带。
    实时跑时还没拍 step 截图（Engine 当前没用上 ``snapshot_before``），所以
    回放也不会有 before 截图。

    **重要**：``screenshot_url`` 必须走 ``/uploads/ui_artifacts/...`` nginx
    静态路径，**不能**走鉴权 API 路径——前端 ``<img src>`` 不带 Authorization
    header（axios interceptor 不参与），鉴权 API 必 401，回放页面会显示
    "截图加载失败"（实际故障，2026-05 修复）。同 video 加载失败的根因。
    """
    from app.modules.ui_automation.execution_service import _artifact_path_to_url

    payload: dict[str, Any] = {
        "type": "step_complete",
        "case_result_id": str(case_id),
        "step_number": step.step_number,
        "status": step.status,
        "tool_calls": list(step.tool_calls or []),
        "tool_calls_count": len(step.tool_calls or []),
        "tokens_used": step.tokens_used,
        "duration_ms": step.duration_ms,
        "error": step.error_message,
        "snapshot_after": step.snapshot_after,
        "ai_reasoning": step.ai_reasoning,
        "assertion": {
            "passed": step.assertion_passed,
            "reason": step.assertion_reason,
            "evidence": step.assertion_evidence,
        },
        "replay": True,
    }
    if step.screenshot_path:
        # 走 nginx 静态路径（与实时模式 ``execution_engine`` 一致），让前端
        # ``<img src>`` 直接出图无需 token。详见 ``_artifact_path_to_url``。
        url = _artifact_path_to_url(step.screenshot_path)
        if url:
            payload["screenshot_url"] = url
    return payload


__all__ = ["replay"]
