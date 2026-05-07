"""触发词召回（Phase 12 / Task 12.2）。

算法：子串大小写不敏感匹配；得分 = 命中 trigger 字符串长度；按分倒序，取前 ``max``。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.skills.models import Skill


def _trigger_score(message_lower: str, triggers: object) -> int:
    if not isinstance(triggers, list):
        return 0
    best = 0
    for raw in triggers:
        if not isinstance(raw, str):
            continue
        needle = raw.strip().lower()
        if len(needle) < 1:
            continue
        if needle in message_lower:
            best = max(best, len(needle))
    return best


async def match_triggers(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    message: str,
    max_matches: int = 3,
) -> list[Skill]:
    """返回触发词命中的 skill（仅 ``activation_mode=trigger``），按得分降序。"""
    if project_id is None or max_matches <= 0:
        return []

    msg_lower = message.lower()
    stmt = (
        select(Skill)
        .where(
            Skill.project_id == project_id,
            Skill.is_enabled.is_(True),
            Skill.activation_mode == "trigger",
        )
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    scored: list[tuple[int, Skill]] = []
    for sk in rows:
        score = _trigger_score(msg_lower, sk.triggers)
        if score > 0:
            scored.append((score, sk))

    scored.sort(key=lambda x: (-x[0], x[1].slug))
    return [sk for _, sk in scored[:max_matches]]


def trigger_match_detail(message: str, triggers: object) -> tuple[int, list[str]]:
    """返回 (最高分, 命中的原始 trigger 文案列表)。"""
    if not isinstance(triggers, list):
        return 0, []
    msg_lower = message.lower()
    matched: list[str] = []
    best = 0
    for raw in triggers:
        if not isinstance(raw, str):
            continue
        needle = raw.strip().lower()
        if len(needle) < 1:
            continue
        if needle in msg_lower:
            matched.append(raw.strip())
            best = max(best, len(needle))
    return best, matched


async def match_triggers_debug(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    message: str,
    *,
    max_matches: int = 5,
) -> list[tuple[Skill, int, list[str]]]:
    """调试：列出 trigger 模式 skill 的得分与命中子串（不限是否达到阈值）。"""
    if project_id is None or max_matches <= 0:
        return []

    stmt = (
        select(Skill)
        .where(
            Skill.project_id == project_id,
            Skill.is_enabled.is_(True),
            Skill.activation_mode == "trigger",
        )
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    scored: list[tuple[int, Skill, list[str]]] = []
    for sk in rows:
        score, hits = trigger_match_detail(message, sk.triggers)
        scored.append((score, sk, hits))

    scored.sort(key=lambda x: (-x[0], x[1].slug))
    out: list[tuple[Skill, int, list[str]]] = []
    for score, sk, hits in scored[:max_matches]:
        out.append((sk, score, hits))
    return out
