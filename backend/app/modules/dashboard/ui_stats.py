"""Task 11.1 — 项目维度 UI 自动化统计聚合。

为 Dashboard 提供"双视图"通过率（业务/执行）+ 可信度分布 + 自造 Top 10 +
平均耗时 / Token 总量 + 最近执行列表。

设计取舍：
- **业务通过率** = passed / (total - data_failure)。把"缺料导致失败"的用例从分母
  里剔除——这部分不是被测系统的问题，而是物料缺失，不应拖低质量信号。这跟
  ``ExecutionDetail`` / ``ExecutionHistory`` / ``UIExecutionCard`` 用同一口径，
  保证用户在 Dashboard 看到的数字跟其他入口一致。
- **执行通过率** = passed / total。原始通过率，给"测试基础设施健康度"这种
  视角看（环境是否稳定、断言是否合理）。
- **Top synthesized keys**：先在 SQL 层用 ``jsonb_array_elements`` 把数组炸开，
  再 ``GROUP BY key`` —— 比拉到 Python 内存再 Counter 至少省一个数量级 IO。
  限制 100 行后只取前 10，避免单个项目上千次执行时的全表扫。
- **avg_duration / total_tokens**：直接 AVG/SUM 到 ``ui_executions`` 表的列上，
  比加字段到 case 级聚合便宜得多（只看终态执行）。
- **recent_executions**：附带每条 execution 的双视图通过率，让前端不用再发 N+1
  的请求；每行包含 case 维度的 confidence_counts（小，可接受）。

无侧效（不写 DB），所有读查询都基于 RLS-friendly 的 project_id 谓词。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.ui_automation.models import UICaseResult, UIExecution
from app.modules.ui_automation.service import (
    _check_project_member,
    _ensure_project_exists,
)

# ─── Pure helpers (unit-testable without DB) ─────────────────────────────


# 整次执行进入 "Terminal" 状态的状态集合（用于"已完成的执行"过滤）。
# 跟 chat_service.TERMINAL_STATUSES 同义，独立列出避免循环 import。
_TERMINAL_STATUSES = ("completed", "failed", "stopped", "aborted_budget")


def compute_pass_rate(passed: int, denominator: int) -> float:
    """两位小数的百分数。分母 0 时返回 0（而不是 None / NaN），让前端能直接渲染。"""
    if denominator <= 0:
        return 0.0
    return round((passed / denominator) * 100, 2)


def aggregate_top_synthesized_keys(
    rows: list[tuple[str, int]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """把 SQL 聚合结果 ``[(key, count), ...]`` 标准化成前端友好的 dict 列表。

    传入已经 GROUP BY 过的行；这里只负责截断 + 形状归一。独立成函数主要为了
    单测：拿一坨 fixture 进来就能断言"按 count 降序、ties 按 key 升序"。
    """
    # 二次稳定排序：count desc, key asc（让 ties 的展示稳定）
    sorted_rows = sorted(rows, key=lambda r: (-int(r[1]), str(r[0])))
    return [{"key": str(k), "count": int(c)} for k, c in sorted_rows[:limit]]


def aggregate_top_keys_from_python(
    case_records: list[list[dict[str, Any]]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Python 兜底实现：当 SQL JSONB 聚合不可用时（测试 / SQLite）使用。

    输入：每个 case 的 synthesized_data 数组，每个数组项形如
    ``{"key": "...", "value": "...", "source": "...", "step_id": "..."}``。
    """
    counter: Counter[str] = Counter()
    for case_arr in case_records:
        if not case_arr:
            continue
        for item in case_arr:
            if isinstance(item, dict):
                key = item.get("key")
                if isinstance(key, str) and key:
                    counter[key] += 1
    return aggregate_top_synthesized_keys(list(counter.items()), limit=limit)


# ─── Main service ────────────────────────────────────────────────────────


async def get_project_ui_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    view: str = "business",
    recent_limit: int = 10,
) -> dict[str, Any]:
    """聚合一个项目的 UI 自动化统计。

    Args:
        view: "business" | "execution"。决定 ``pass_rate`` 用哪个口径。两个口径
            的数字都会同时返回（前端切换不重新请求），``pass_rate`` 只是 echo
            选中那个，方便前端透传。

    Returns:
        见模块 docstring 中的字段说明。
    """
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    if view not in ("business", "execution"):
        view = "business"

    # ─ 1. case-level 聚合：状态 × 数据可信度 ──────────────────────────
    # 一次 query 拿全部 case_results 的 status + data_confidence 计数。比起跑 6
    # 次 COUNT(*) WHERE 这种"逐条"查询，这里一次 GROUP BY 就够；项目有
    # 几千条 cases 也只是毫秒级。
    status_dim_q = (
        select(
            UICaseResult.status,
            UICaseResult.data_confidence,
            func.count().label("cnt"),
        )
        .join(UIExecution, UICaseResult.execution_id == UIExecution.id)
        .where(UIExecution.project_id == project_id)
        .group_by(UICaseResult.status, UICaseResult.data_confidence)
    )
    rows = (await db.execute(status_dim_q)).all()

    total_cases = 0
    passed_cases = 0
    failed_cases = 0
    skipped_cases = 0
    confidence_distribution: dict[str, int] = {
        "reliable": 0,
        "synthesized": 0,
        "data_failure": 0,
    }
    for status, confidence, cnt in rows:
        total_cases += cnt
        if status == "passed":
            passed_cases += cnt
        elif status == "failed":
            failed_cases += cnt
        elif status == "skipped":
            skipped_cases += cnt
        # 把未识别的 confidence 归到 reliable（兼容历史脏数据）
        bucket = confidence if confidence in confidence_distribution else "reliable"
        confidence_distribution[bucket] += cnt

    excluded_data_failure_cases = confidence_distribution["data_failure"]
    business_total = max(0, total_cases - excluded_data_failure_cases)

    business_pass_rate = compute_pass_rate(passed_cases, business_total)
    execution_pass_rate = compute_pass_rate(passed_cases, total_cases)
    pass_rate = business_pass_rate if view == "business" else execution_pass_rate

    # ─ 2. execution-level 聚合：avg_duration / total_tokens / 数量 ─────
    # 同时拿 ``task_pass_rate``（任务级通过率）：=
    #   completed 状态的执行数 / 全部终态执行数。
    # 这个口径回答用户视角的「整体通过率」——一次执行整体跑通才算通过；
    # 仅 case-level 的 business / execution 通过率没法反映"前置步骤失败 → 0
    # 用例产出"这种场景，会导致两条失败任务还显示 100% 通过率（验收反馈）。
    exec_agg_q = select(
        func.count().label("exec_count"),
        func.coalesce(func.sum(UIExecution.tokens_total), 0).label("tokens_sum"),
        func.avg(UIExecution.duration_ms).label("dur_avg"),
        func.sum(
            case((UIExecution.status == "completed", 1), else_=0),
        ).label("succeeded_exec_count"),
    ).where(
        UIExecution.project_id == project_id,
        UIExecution.status.in_(_TERMINAL_STATUSES),
    )
    exec_count, tokens_sum, dur_avg, succeeded_exec_count = (
        await db.execute(exec_agg_q)
    ).one()
    succeeded_exec_count = int(succeeded_exec_count or 0)
    task_pass_rate = compute_pass_rate(succeeded_exec_count, int(exec_count or 0))

    # ─ 3. Top synthesized keys（SQL 优先，PG 上跑 jsonb_array_elements）─
    top_keys = await _query_top_synthesized_keys(db, project_id, limit=10)

    # ─ 4. 最近 N 个执行（含 per-execution 双通过率）──────────────────
    recent_limit = max(1, min(recent_limit, 30))
    recent_executions = await _query_recent_executions(
        db, project_id, limit=recent_limit,
    )

    return {
        "view": view,
        "pass_rate": pass_rate,
        "business_pass_rate": business_pass_rate,
        "execution_pass_rate": execution_pass_rate,
        # 任务级通过率：completed 执行数 / 全部终态执行数。
        # 解决「前置步骤失败 → 0 用例产出」时 case-level 通过率失真问题。
        "task_pass_rate": task_pass_rate,
        "succeeded_exec_count": succeeded_exec_count,
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "skipped_cases": skipped_cases,
        "excluded_data_failure_cases": excluded_data_failure_cases,
        "confidence_distribution": confidence_distribution,
        "top_synthesized_keys": top_keys,
        "execution_count": int(exec_count or 0),
        "total_tokens": int(tokens_sum or 0),
        "avg_duration_ms": float(dur_avg) if dur_avg is not None else None,
        "recent_executions": recent_executions,
    }


async def _query_top_synthesized_keys(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """用 PG 的 ``jsonb_array_elements`` 把 ``synthesized_data`` 数组炸开，
    再按 ``key`` 计数 → 返回 Top N。

    SQL 失败兜底（典型：测试环境用了 SQLite）：拉所有 case 的 JSONB 字段到
    Python，调 ``aggregate_top_keys_from_python``。
    """
    # 直接走 raw SQL 因为 SQLAlchemy ORM 表达 jsonb_array_elements 比较拗口，
    # 而且这条语句很简单——只读取，没动态拼接，安全。
    # NOTE: ``sd`` alias 必须在 jsonb_array_elements 后；外层 GROUP BY 用 ``key``
    # 列名（jsonb 的 ``->>`` 拿 text）。
    sql = text(
        """
        SELECT (sd ->> 'key') AS k, COUNT(*) AS cnt
        FROM ui_case_results c
        JOIN ui_executions e ON c.execution_id = e.id
        CROSS JOIN LATERAL jsonb_array_elements(c.synthesized_data) AS sd
        WHERE e.project_id = :pid
          AND (sd ->> 'key') IS NOT NULL
        GROUP BY k
        ORDER BY cnt DESC, k ASC
        LIMIT :lim
        """,
    )
    try:
        result = await db.execute(sql, {"pid": str(project_id), "lim": limit})
        return aggregate_top_synthesized_keys(
            [(r[0], r[1]) for r in result.all()],
            limit=limit,
        )
    except Exception:  # pragma: no cover — 兜底分支主要给非 PG 后端
        # 退化到 Python 聚合：拉所有 case 的 synthesized_data
        fallback_q = (
            select(UICaseResult.synthesized_data)
            .join(UIExecution, UICaseResult.execution_id == UIExecution.id)
            .where(UIExecution.project_id == project_id)
        )
        rows = (await db.execute(fallback_q)).scalars().all()
        return aggregate_top_keys_from_python(
            [r if isinstance(r, list) else [] for r in rows],
            limit=limit,
        )


async def _query_recent_executions(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """最近 N 次执行的简化卡片，含 per-execution 的双视图通过率 + 可信度计数。

    用 LEFT JOIN + GROUP BY 一次 query 把 case 维度的可信度计数也带回来，
    避免前端拿到列表后再 N+1 拉详情。
    """
    # 子查询：每个 execution 的可信度计数（passed_cases 直接用 ui_executions 表里的）
    confidence_q = (
        select(
            UICaseResult.execution_id.label("eid"),
            func.sum(
                case((UICaseResult.data_confidence == "data_failure", 1), else_=0),
            ).label("data_failure_cases"),
            func.sum(
                case((UICaseResult.data_confidence == "synthesized", 1), else_=0),
            ).label("synthesized_cases"),
            func.sum(
                case((UICaseResult.data_confidence == "reliable", 1), else_=0),
            ).label("reliable_cases"),
        )
        .group_by(UICaseResult.execution_id)
        .subquery()
    )

    q = (
        select(
            UIExecution,
            confidence_q.c.data_failure_cases,
            confidence_q.c.synthesized_cases,
            confidence_q.c.reliable_cases,
        )
        .outerjoin(confidence_q, confidence_q.c.eid == UIExecution.id)
        .where(UIExecution.project_id == project_id)
        .order_by(UIExecution.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(q)).all()

    out: list[dict[str, Any]] = []
    for exec_row, data_failure, synthesized, reliable in rows:
        total = exec_row.total_cases or 0
        passed = exec_row.passed_cases or 0
        failed = exec_row.failed_cases or 0
        skipped = exec_row.skipped_cases or 0
        df = int(data_failure or 0)
        sn = int(synthesized or 0)
        rl = int(reliable or 0)

        out.append({
            "id": str(exec_row.id),
            "status": exec_row.status,
            "mode": exec_row.mode,
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "skipped_cases": skipped,
            "duration_ms": exec_row.duration_ms,
            "tokens_total": exec_row.tokens_total,
            "started_at": _iso(exec_row.started_at),
            "completed_at": _iso(exec_row.completed_at),
            "created_at": _iso(exec_row.created_at),
            "business_pass_rate": compute_pass_rate(passed, max(0, total - df)),
            "execution_pass_rate": compute_pass_rate(passed, total),
            "data_failure_cases": df,
            "synthesized_cases": sn,
            "reliable_cases": rl,
        })
    return out


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None
