"""Skill 使用审计与聚合统计（Task 12.4 / 12.6）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.skills.models import Skill, SkillUsageLog


async def log_usage(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID | None,
    activation_reason: str,
    session_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    matched_trigger: str | None = None,
    tokens_consumed: int | None = None,
    outcome: str = "success",
    error_message: str | None = None,
    skill_db_version: int | None = None,
) -> SkillUsageLog:
    row = SkillUsageLog(
        skill_id=skill_id,
        skill_db_version=skill_db_version,
        session_id=session_id,
        message_id=message_id,
        activation_reason=activation_reason,
        matched_trigger=matched_trigger,
        tokens_consumed=tokens_consumed,
        outcome=outcome,
        error_message=error_message,
    )
    db.add(row)
    await db.flush()
    return row


async def aggregate_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    days: int = 7,
) -> dict[str, dict[str, float | int]]:
    """按 skill_id 聚合最近 ``days`` 天记录：count + success_rate + avg_tokens。"""
    if days < 1:
        days = 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ok_sum = func.sum(case((SkillUsageLog.outcome == "success", 1), else_=0)).label("ok")
    tokens_avg = func.avg(SkillUsageLog.tokens_consumed).label("avg_tokens")

    stmt = (
        select(SkillUsageLog.skill_id, func.count().label("cnt"), ok_sum, tokens_avg)
        .join(Skill, SkillUsageLog.skill_id == Skill.id)
        .where(
            Skill.project_id == project_id,
            SkillUsageLog.skill_id.isnot(None),
            SkillUsageLog.created_at >= cutoff,
        )
        .group_by(SkillUsageLog.skill_id)
    )
    result = await db.execute(stmt)
    out: dict[str, dict[str, float | int]] = {}
    for skill_id, cnt, ok, avg_tok in result.all():
        if skill_id is None:
            continue
        n = int(cnt or 0)
        successes = int(ok or 0)
        rate = float(successes / n) if n else 0.0
        out[str(skill_id)] = {
            "count": n,
            "success_rate": round(rate, 4),
            "avg_tokens": round(float(avg_tok), 2) if avg_tok is not None else 0.0,
        }
    return out


async def daily_trend(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    days: int = 30,
    skill_id: uuid.UUID | None = None,
) -> dict[str, list[dict[str, int | str]]]:
    """按 skill_id 拉近 ``days`` 天每天调用量；可选限定单个 skill。

    返回 ``{"<skill_id>": [{"date": "2026-05-01", "count": 3}, ...]}``。
    """
    if days < 1:
        days = 1
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    day_bucket = func.date_trunc("day", SkillUsageLog.created_at).label("day")

    base = (
        select(SkillUsageLog.skill_id, day_bucket, func.count().label("cnt"))
        .join(Skill, SkillUsageLog.skill_id == Skill.id)
        .where(
            Skill.project_id == project_id,
            SkillUsageLog.skill_id.isnot(None),
            SkillUsageLog.created_at >= cutoff,
        )
        .group_by(SkillUsageLog.skill_id, day_bucket)
        .order_by(SkillUsageLog.skill_id, day_bucket)
    )
    if skill_id is not None:
        base = base.where(SkillUsageLog.skill_id == skill_id)

    out: dict[str, list[dict[str, int | str]]] = {}
    for sid, day, cnt in (await db.execute(base)).all():
        if sid is None:
            continue
        key = str(sid)
        out.setdefault(key, []).append({
            "date": day.date().isoformat() if hasattr(day, "date") else str(day)[:10],
            "count": int(cnt or 0),
        })
    return out


async def list_failures(
    db: AsyncSession,
    skill_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[dict]:
    """列出某 skill 最近 N 条失败记录（含 error_message + 关联 message_id）。"""
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200
    stmt = (
        select(SkillUsageLog)
        .where(
            SkillUsageLog.skill_id == skill_id,
            SkillUsageLog.outcome != "success",
        )
        .order_by(SkillUsageLog.created_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    out: list[dict] = []
    for r in rows:
        out.append({
            "id": str(r.id),
            "session_id": str(r.session_id) if r.session_id else None,
            "message_id": str(r.message_id) if r.message_id else None,
            "activation_reason": r.activation_reason,
            "outcome": r.outcome,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out
