"""三策略级联用例匹配（Phase 13 / Task 13.2）。

设计依据：``docs/PHASE3_DESIGN.md §10.2.1 / §10.2.2``，对应 PHASE3 实施
计划 Task 13.2 "search_test_cases 三策略级联"。

策略与置信度：

| 策略 | 触发 | relevance_score | matched_via |
|---|---|---|---|
| ① ID 精确 | query 含 ``#NNN`` / ``TC-NNN`` / 直接 UUID | 1.00 | ``id_exact`` |
| ② title + tags 模糊 | 普通关键字 | 0.40 ~ 0.95（含 tag 命中加权） | ``title_fulltext`` / ``tag_match`` |
| ③ 步骤内容召回 | 二/三策略均无命中或仅有少数命中 | 0.30 ~ 0.60 | ``step_content`` |

去重规则：按 ``case.id`` 去重，**保留最高 score 的 matched_via**——同一 case
被多个策略命中时，得分汇总（最高分 + 0.05 × 其它命中策略数，封顶 1.0）。

策略 3（步骤召回）M1 走 ``ilike`` 兜底；后续 M2 接入 pgvector 做向量召回时
仅替换该函数实现，前端 / LLM 协议（CaseSummary.relevance_score）不变。
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.testcases.models import Testcase, TestcaseStep

logger = logging.getLogger(__name__)


class CaseMatchStrategy(str, Enum):
    """便于上游聚合统计 / 调试日志区分。"""

    ID_EXACT = "id_exact"
    TITLE_FULLTEXT = "title_fulltext"
    TAG_MATCH = "tag_match"
    STEP_CONTENT = "step_content"
    RECENT_FALLBACK = "recent_fallback"


@dataclass(slots=True)
class CaseCandidate:
    """match_test_cases 返回的单条候选。"""

    case: Testcase
    relevance_score: float
    matched_via: list[CaseMatchStrategy] = field(default_factory=list)


# ───────────────── 策略 1：ID / 编号精确 ─────────────────


_CASE_NO_PATTERN = re.compile(r"(?:#|TC[-_]?)(\d+)", re.IGNORECASE)
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _extract_case_nos(query: str) -> list[int]:
    nos: list[int] = []
    for m in _CASE_NO_PATTERN.finditer(query or ""):
        try:
            nos.append(int(m.group(1)))
        except (TypeError, ValueError):
            continue
    return nos


def _extract_uuids(query: str) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for m in _UUID_PATTERN.finditer(query or ""):
        try:
            out.append(uuid.UUID(m.group(0)))
        except (TypeError, ValueError):
            continue
    return out


async def _match_by_id(
    db: AsyncSession, query: str, project_id: uuid.UUID,
) -> list[CaseCandidate]:
    case_nos = _extract_case_nos(query)
    case_uuids = _extract_uuids(query)
    if not case_nos and not case_uuids:
        return []

    conds = []
    if case_nos:
        conds.append(Testcase.case_no.in_(case_nos))
    if case_uuids:
        conds.append(Testcase.id.in_(case_uuids))

    stmt = (
        select(Testcase)
        .where(Testcase.project_id == project_id, or_(*conds))
        .limit(20)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        CaseCandidate(
            case=tc,
            relevance_score=1.0,
            matched_via=[CaseMatchStrategy.ID_EXACT],
        )
        for tc in rows
    ]


# ───────────────── 策略 2：title + tags 模糊 ─────────────────


# 中英文常见的"无意义停用词"——避免一句话被切到只剩 "的/和/了" 误命中所有
# 用例。M2 task 升级 ts_vector / 分词器后会换成更准确的实现。
_STOPWORDS = frozenset({
    "的", "了", "和", "与", "及", "或", "在", "是", "把", "给", "用",
    "我", "你", "他", "我们", "你们",
    "用例", "测试", "case", "test", "tc",
    "请", "帮", "帮我", "麻烦",
    "跑", "跑下", "跑一下", "执行", "运行", "执行一下",
    "查询", "查看", "看下", "看看",
    "a", "an", "the", "to", "of", "for", "in", "on",
})


def _tokenize_query(query: str, *, max_tokens: int = 6) -> list[str]:
    """简单 tokenization：去停用词 + 截断长度。

    这里**不**做 jieba 分词——一来增加依赖，二来 jieba 误分会让 ilike 反而错过；
    M1 直接按"原始 query 整段 ilike"+"按空格 / 标点切的 token ilike OR" 的双重命中策略，
    经验上对中文短 query（"登录用例"/"回归用例"）已经够用。
    """
    if not query:
        return []
    raw = re.split(r"[\s,，;；。.!！?？/\\\"'()（）\[\]【】]+", query.strip())
    seen: set[str] = set()
    out: list[str] = []
    for token in raw:
        token = token.strip()
        if not token or len(token) < 2:
            continue
        if token.lower() in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max_tokens:
            break
    return out


def _title_relevance(title: str, query: str, tokens: list[str]) -> float:
    """0.40 ~ 0.95 之间的启发式分数。

    规则：
    - title 完整包含原始 query → 0.85
    - title 命中第一个 token → 0.65；命中两个及以上 → 0.85
    - 仅命中模糊片段 → 0.40 兜底
    """
    if not title:
        return 0.40
    title_l = title.lower()
    q_l = (query or "").lower().strip()
    if q_l and q_l in title_l:
        return 0.85
    hits = sum(1 for t in tokens if t.lower() in title_l)
    if hits >= 2:
        return 0.85
    if hits == 1:
        return 0.65
    return 0.40


async def _match_by_title_and_tags(
    db: AsyncSession, query: str, project_id: uuid.UUID, *, limit: int,
) -> tuple[list[CaseCandidate], list[CaseCandidate]]:
    """返回 ``(by_title_candidates, by_tag_candidates)``——两路独立结果，
    上游聚合时按 case_id 合并 + score 汇总。"""

    if not query:
        return [], []

    tokens = _tokenize_query(query)
    title_conds = []
    if query.strip():
        title_conds.append(Testcase.title.ilike(f"%{query.strip()}%"))
    for t in tokens:
        title_conds.append(Testcase.title.ilike(f"%{t}%"))
    if not title_conds:
        return [], []

    title_stmt = (
        select(Testcase)
        .where(
            Testcase.project_id == project_id,
            or_(*title_conds),
        )
        .order_by(Testcase.updated_at.desc())
        .limit(limit * 2)
    )
    title_rows = list((await db.execute(title_stmt)).scalars().all())

    title_cands: list[CaseCandidate] = []
    for tc in title_rows:
        score = _title_relevance(tc.title or "", query, tokens)
        title_cands.append(
            CaseCandidate(
                case=tc,
                relevance_score=score,
                matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
            ),
        )

    # tags 命中：用 PostgreSQL JSONB ``?|`` (any of array) 算子。M1 兜底走
    # ``string-array @> '["tag"]'`` 形式——SQLAlchemy 通过 ``func.jsonb_exists_any``
    # 暴露。query 里出现的 token 直接当作潜在 tag。
    tag_cands: list[CaseCandidate] = []
    if tokens:
        # 仅取 query 出现的"短语"作为 tag candidates。tags 一般是 ASCII 标签，
        # 中文 token 不太会命中——但保留以便用户用纯中文命名 tag。
        tag_stmt = (
            select(Testcase)
            .where(
                Testcase.project_id == project_id,
                func.jsonb_exists_any(Testcase.tags, list(tokens)),
            )
            .limit(limit * 2)
        )
        try:
            tag_rows = list((await db.execute(tag_stmt)).scalars().all())
        except Exception:  # noqa: BLE001
            # 异常路径（极少见，例：sqlite 单测 fallback）—— 静默吞掉，保留
            # title 主力召回。
            logger.warning("tag-based query failed; skipping tag match")
            tag_rows = []
        for tc in tag_rows:
            tag_cands.append(
                CaseCandidate(
                    case=tc,
                    # tag 直接命中给较高 score——用户提"回归用例"直奔 tag 路径
                    # 比 title 模糊更精准。
                    relevance_score=0.90,
                    matched_via=[CaseMatchStrategy.TAG_MATCH],
                ),
            )

    return title_cands, tag_cands


# ───────────────── 策略 3：步骤内容召回 ─────────────────


async def _match_by_step_content(
    db: AsyncSession, query: str, project_id: uuid.UUID, *, limit: int,
) -> list[CaseCandidate]:
    """对 ``testcase_steps.action / expected_result`` 做 ilike 模糊召回。

    M1 兜底实现；M2 task 13.6 会升级为 ``ts_vector`` / pgvector 召回。返回的
    score 偏低（0.30 ~ 0.60），策略 1 / 2 命中时一般会自动盖过本路径。
    """
    if not query:
        return []
    tokens = _tokenize_query(query)
    if not tokens:
        return []

    step_conds = []
    for t in tokens:
        like = f"%{t}%"
        step_conds.append(TestcaseStep.action.ilike(like))
        step_conds.append(TestcaseStep.expected_result.ilike(like))

    stmt = (
        select(Testcase)
        .options(selectinload(Testcase.steps))
        .join(TestcaseStep, TestcaseStep.testcase_id == Testcase.id)
        .where(
            Testcase.project_id == project_id,
            or_(*step_conds),
        )
        .distinct()
        .limit(limit * 2)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    out: list[CaseCandidate] = []
    for tc in rows:
        # 简易加权：每个 token 命中一个 step 加 0.10，封顶 0.60。
        step_text = " ".join(
            (s.action or "") + " " + (s.expected_result or "")
            for s in (tc.steps or [])
        ).lower()
        hits = sum(1 for t in tokens if t.lower() in step_text)
        score = min(0.30 + 0.10 * hits, 0.60)
        out.append(
            CaseCandidate(
                case=tc,
                relevance_score=score,
                matched_via=[CaseMatchStrategy.STEP_CONTENT],
            ),
        )
    return out


# ───────────────── 兜底：query 为空 → 最近若干条 ─────────────────


async def _match_recent(
    db: AsyncSession, project_id: uuid.UUID, *, limit: int,
) -> list[CaseCandidate]:
    stmt = (
        select(Testcase)
        .where(Testcase.project_id == project_id)
        .order_by(Testcase.updated_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        CaseCandidate(
            case=tc,
            relevance_score=0.20,
            matched_via=[CaseMatchStrategy.RECENT_FALLBACK],
        )
        for tc in rows
    ]


# ───────────────── 聚合 / 去重 / 排序 ─────────────────


def _merge_candidates(*groups: list[CaseCandidate]) -> list[CaseCandidate]:
    """按 ``case.id`` 合并：最高分 + 其它命中策略加 0.05 / 路径，封顶 1.0。"""
    by_id: dict[uuid.UUID, CaseCandidate] = {}
    for group in groups:
        for c in group:
            cur = by_id.get(c.case.id)
            if cur is None:
                by_id[c.case.id] = CaseCandidate(
                    case=c.case,
                    relevance_score=c.relevance_score,
                    matched_via=list(c.matched_via),
                )
                continue
            # 保留更高分 + 累加 matched_via（去重）
            higher = max(cur.relevance_score, c.relevance_score)
            extra = 0.05 * len([m for m in c.matched_via if m not in cur.matched_via])
            cur.relevance_score = min(1.0, higher + extra)
            for m in c.matched_via:
                if m not in cur.matched_via:
                    cur.matched_via.append(m)
    return list(by_id.values())


def _sort_candidates(items: list[CaseCandidate]) -> list[CaseCandidate]:
    """先按 score desc，同分按 case.updated_at desc 倒序。"""
    return sorted(
        items,
        key=lambda c: (
            -c.relevance_score,
            -(getattr(c.case, "updated_at", None).timestamp()
              if getattr(c.case, "updated_at", None) else 0),
        ),
    )


async def match_test_cases(
    db: AsyncSession,
    query: str,
    project_id: uuid.UUID,
    *,
    limit: int = 10,
) -> list[CaseCandidate]:
    """三策略级联入口。

    顺序：
    1. ID 模式（``#NNN`` / ``TC-NNN`` / UUID）—— 直接命中即返回，跳过其它策略
       （避免编号被当成关键字误命中其它用例）
    2. title + tags 模糊
    3. 步骤内容召回（兜底）

    若 ``query`` 为空 → 返回最近更新若干条（``RECENT_FALLBACK``）。
    无任何命中 → 返回空列表（上游 LLM 按"未找到匹配用例"提示用户）。
    """
    limit = max(1, min(int(limit or 10), 30))
    q = (query or "").strip()

    if not q:
        return (await _match_recent(db, project_id, limit=limit))[:limit]

    id_cands = await _match_by_id(db, q, project_id)
    if id_cands:
        return _sort_candidates(_merge_candidates(id_cands))[:limit]

    title_cands, tag_cands = await _match_by_title_and_tags(
        db, q, project_id, limit=limit,
    )
    step_cands = await _match_by_step_content(db, q, project_id, limit=limit)

    merged = _merge_candidates(id_cands, title_cands, tag_cands, step_cands)
    return _sort_candidates(merged)[:limit]


# ───────────────── 工具函数：暴露给 search_test_cases tool ─────────────────


def candidate_to_dict(c: CaseCandidate) -> dict[str, Any]:
    """转换为 ``CaseSummary`` 兼容的 dict（供 search_test_cases tool 装配）。"""
    return {
        "id": str(c.case.id),
        "case_no": c.case.case_no,
        "title": c.case.title,
        "priority": c.case.priority,
        "status": c.case.status,
        "relevance_score": round(float(c.relevance_score), 3),
        "matched_via": [m.value for m in c.matched_via],
    }
