"""Phase 13 / Task 13.2 — case_matcher 三策略级联单测。

DoD：覆盖 6 种 query 形态：
  1. ``#123`` → 策略 1（id_exact）
  2. ``TC-0042`` → 策略 1（id_exact）
  3. UUID 字面量 → 策略 1（id_exact）
  4. ``"登录用例"`` → 策略 2（title_fulltext）
  5. ``"回归"`` → 策略 2（tag_match）
  6. ``"点击登录按钮"`` → 策略 3（step_content）
  + 边界：空 query → recent_fallback；完全不命中 → 空列表
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.skills.builtin.ui_automation.matchers import case_matcher
from app.modules.skills.builtin.ui_automation.matchers.case_matcher import (
    CaseMatchStrategy,
    _extract_case_nos,
    _extract_uuids,
    _merge_candidates,
    _title_relevance,
    _tokenize_query,
    match_test_cases,
)

# ───────────────── 纯函数测试 ─────────────────


def test_extract_case_nos_handles_all_variants() -> None:
    assert _extract_case_nos("跑 #123") == [123]
    assert _extract_case_nos("TC-0042 跑下") == [42]
    assert _extract_case_nos("TC_0007 修复回归") == [7]
    assert _extract_case_nos("帮我跑 #1 #2 #3") == [1, 2, 3]
    assert _extract_case_nos("登录用例") == []


def test_extract_uuids_finds_full_lowercase_uuid() -> None:
    full = uuid.uuid4()
    out = _extract_uuids(f"执行 {full}")
    assert out == [full]
    assert _extract_uuids("没有 uuid") == []


def test_tokenize_query_drops_stopwords_and_dedupes() -> None:
    tokens = _tokenize_query("帮我跑下 登录 用例 测试 测试")
    # 中英文停用词（"帮我跑下"/"用例"/"测试"）应被过滤；剩 "登录"
    assert "登录" in tokens
    assert "用例" not in tokens
    assert "测试" not in tokens


def test_title_relevance_full_query_match_gets_high_score() -> None:
    score = _title_relevance("登录-验证账号密码", "登录", ["登录"])
    assert score == 0.85


def test_title_relevance_two_token_match_caps_high_score() -> None:
    score = _title_relevance(
        "用户管理-新增账号 登录页校验",
        "新增账号 登录",
        ["新增账号", "登录"],
    )
    assert score == 0.85


def test_title_relevance_no_match_returns_floor() -> None:
    score = _title_relevance("订单-退款流程", "完全不相关", ["完全不相关"])
    assert score == 0.40


def test_merge_candidates_deduplicates_and_aggregates_score() -> None:
    case = MagicMock()
    case.id = uuid.uuid4()
    case.updated_at = None
    c1 = case_matcher.CaseCandidate(
        case=case, relevance_score=0.85,
        matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
    )
    c2 = case_matcher.CaseCandidate(
        case=case, relevance_score=0.40,
        matched_via=[CaseMatchStrategy.STEP_CONTENT],
    )
    merged = _merge_candidates([c1], [c2])
    assert len(merged) == 1
    # 0.85 + 0.05（额外路径加分）= 0.90
    assert pytest.approx(merged[0].relevance_score, abs=1e-3) == 0.90
    assert set(merged[0].matched_via) == {
        CaseMatchStrategy.TITLE_FULLTEXT,
        CaseMatchStrategy.STEP_CONTENT,
    }


# ───────────────── 集成测试（mock DB）─────────────────


@dataclass
class _StubCase:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    case_no: int = 1
    title: str = ""
    priority: str = "medium"
    status: str = "active"
    project_id: uuid.UUID = field(default_factory=uuid.uuid4)
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime(2026, 5, 7))
    steps: list = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _stub_db_returning_per_call(call_results: list[list[Any]]) -> AsyncMock:
    """每次 ``db.execute`` 返回 ``call_results`` 的下一段 ``scalars().all()`` 结果。"""
    db = AsyncMock()
    iterator = iter(call_results)

    async def _execute(_stmt):  # noqa: ANN001
        try:
            rows = next(iterator)
        except StopIteration:
            rows = []
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=rows)
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)
        result.first = MagicMock(return_value=None)
        return result

    db.execute = _execute  # type: ignore[assignment]
    return db


@pytest.mark.asyncio
async def test_match_id_exact_short_circuits_other_strategies() -> None:
    """策略 1 命中后**不再**跑策略 2 / 3，避免编号被当成关键字误命中其它用例。"""
    pid = uuid.uuid4()
    target = _StubCase(case_no=123, title="登录-验证账号密码")
    db = _stub_db_returning_per_call([[target]])  # 只期望被调一次

    out = await match_test_cases(db, "执行 #123", pid)

    assert len(out) == 1
    assert out[0].case is target
    assert out[0].relevance_score == 1.0
    assert out[0].matched_via == [CaseMatchStrategy.ID_EXACT]


@pytest.mark.asyncio
async def test_match_tc_dash_pattern_also_short_circuits() -> None:
    pid = uuid.uuid4()
    target = _StubCase(case_no=42, title="新建用户")
    db = _stub_db_returning_per_call([[target]])

    out = await match_test_cases(db, "TC-0042 跑下", pid)

    assert out[0].relevance_score == 1.0
    assert CaseMatchStrategy.ID_EXACT in out[0].matched_via


@pytest.mark.asyncio
async def test_match_uuid_literal_short_circuits() -> None:
    pid = uuid.uuid4()
    target_id = uuid.uuid4()
    target = _StubCase(id=target_id, case_no=99, title="t")
    db = _stub_db_returning_per_call([[target]])

    out = await match_test_cases(db, f"跑 {target_id}", pid)

    assert out[0].case.id == target_id
    assert CaseMatchStrategy.ID_EXACT in out[0].matched_via


@pytest.mark.asyncio
async def test_match_title_fulltext_natural_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'登录用例'：策略 1 不命中（无 #编号）→ 落到策略 2 title 模糊。"""
    pid = uuid.uuid4()
    hit = _StubCase(case_no=1, title="登录-验证账号密码")

    async def _id(*_a, **_k):
        return []

    async def _title_tag(*_a, **_k):
        return (
            [
                case_matcher.CaseCandidate(
                    case=hit, relevance_score=0.65,
                    matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
                ),
            ],
            [],
        )

    async def _step(*_a, **_k):
        return []

    monkeypatch.setattr(case_matcher, "_match_by_id", _id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _step)

    out = await match_test_cases(AsyncMock(), "登录用例", pid)
    assert len(out) == 1
    assert out[0].case.title == "登录-验证账号密码"
    assert CaseMatchStrategy.TITLE_FULLTEXT in out[0].matched_via
    assert 0.40 <= out[0].relevance_score <= 0.95


@pytest.mark.asyncio
async def test_match_tag_route_high_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """'回归用例'：title 不命中 → tag 命中 → 策略 2 给 0.90 高分。"""
    pid = uuid.uuid4()
    tagged = _StubCase(case_no=2, title="支付流程", tags=["回归"])

    async def _id(*_a, **_k):
        return []

    async def _title_tag(*_a, **_k):
        return (
            [],
            [
                case_matcher.CaseCandidate(
                    case=tagged, relevance_score=0.90,
                    matched_via=[CaseMatchStrategy.TAG_MATCH],
                ),
            ],
        )

    async def _step(*_a, **_k):
        return []

    monkeypatch.setattr(case_matcher, "_match_by_id", _id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _step)

    out = await match_test_cases(AsyncMock(), "回归 用例", pid)
    assert len(out) == 1
    assert out[0].case is tagged
    assert CaseMatchStrategy.TAG_MATCH in out[0].matched_via
    assert out[0].relevance_score >= 0.90


@pytest.mark.asyncio
async def test_match_step_content_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """步骤内容召回：title / tag 都不命中时启用，得分 ≤ 0.60。"""
    pid = uuid.uuid4()
    target = _StubCase(case_no=3, title="新功能 A")

    async def _id(*_a, **_k):
        return []

    async def _title_tag(*_a, **_k):
        return ([], [])

    async def _step(*_a, **_k):
        return [
            case_matcher.CaseCandidate(
                case=target, relevance_score=0.50,
                matched_via=[CaseMatchStrategy.STEP_CONTENT],
            ),
        ]

    monkeypatch.setattr(case_matcher, "_match_by_id", _id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _step)

    out = await match_test_cases(AsyncMock(), "点击登录按钮", pid)
    assert len(out) == 1
    assert out[0].case is target
    assert CaseMatchStrategy.STEP_CONTENT in out[0].matched_via
    assert out[0].relevance_score <= 0.60


@pytest.mark.asyncio
async def test_match_empty_query_returns_recent_fallback() -> None:
    pid = uuid.uuid4()
    rec = _StubCase(case_no=4, title="最近更新")
    db = _stub_db_returning_per_call([[rec]])

    out = await match_test_cases(db, "", pid)
    assert len(out) == 1
    assert CaseMatchStrategy.RECENT_FALLBACK in out[0].matched_via


@pytest.mark.asyncio
async def test_match_no_results_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有策略都 0 命中 → 返回空列表（上游 LLM 走"未找到匹配用例"提示）。"""
    pid = uuid.uuid4()

    async def _empty_id(*_a, **_k):
        return []

    async def _empty_title_tag(*_a, **_k):
        return ([], [])

    async def _empty_step(*_a, **_k):
        return []

    monkeypatch.setattr(case_matcher, "_match_by_id", _empty_id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _empty_title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _empty_step)

    out = await match_test_cases(AsyncMock(), "完全不存在的关键词xyz", pid)
    assert out == []


@pytest.mark.asyncio
async def test_match_score_sorted_descending(monkeypatch: pytest.MonkeyPatch) -> None:
    """多条候选时按 relevance_score 降序返回。"""
    pid = uuid.uuid4()
    high = _StubCase(case_no=10, title="登录-完整匹配 登录")
    low = _StubCase(case_no=11, title="无关用例")

    async def _id(*_a, **_k):
        return []

    async def _title_tag(*_a, **_k):
        return (
            [
                case_matcher.CaseCandidate(
                    case=high, relevance_score=0.85,
                    matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
                ),
                case_matcher.CaseCandidate(
                    case=low, relevance_score=0.40,
                    matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
                ),
            ],
            [],
        )

    async def _step(*_a, **_k):
        return [
            case_matcher.CaseCandidate(
                case=low, relevance_score=0.50,
                matched_via=[CaseMatchStrategy.STEP_CONTENT],
            ),
        ]

    monkeypatch.setattr(case_matcher, "_match_by_id", _id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _step)

    out = await match_test_cases(AsyncMock(), "登录", pid)
    assert [c.case.case_no for c in out][0] == 10
    assert out[0].relevance_score >= out[-1].relevance_score


@pytest.mark.asyncio
async def test_match_dedup_across_strategies_keeps_max_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 case 同时被 title / step 命中 → 保留最高分 + 累加 matched_via。"""
    pid = uuid.uuid4()
    same = _StubCase(case_no=7, title="登录-验证账号密码")

    async def _id(*_a, **_k):
        return []

    async def _title_tag(*_a, **_k):
        return (
            [
                case_matcher.CaseCandidate(
                    case=same, relevance_score=0.85,
                    matched_via=[CaseMatchStrategy.TITLE_FULLTEXT],
                ),
            ],
            [],
        )

    async def _step(*_a, **_k):
        return [
            case_matcher.CaseCandidate(
                case=same, relevance_score=0.50,
                matched_via=[CaseMatchStrategy.STEP_CONTENT],
            ),
        ]

    monkeypatch.setattr(case_matcher, "_match_by_id", _id)
    monkeypatch.setattr(case_matcher, "_match_by_title_and_tags", _title_tag)
    monkeypatch.setattr(case_matcher, "_match_by_step_content", _step)

    out = await match_test_cases(AsyncMock(), "登录", pid)
    assert len(out) == 1
    assert out[0].relevance_score >= 0.85
    via = set(out[0].matched_via)
    assert CaseMatchStrategy.TITLE_FULLTEXT in via
    assert CaseMatchStrategy.STEP_CONTENT in via
