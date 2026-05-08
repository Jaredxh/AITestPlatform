"""Phase 13 / Task 13.0 — IntentClassifier Layer 1 + Layer 2 + cache 单测。

设计 DoD：
- Layer 1 规则覆盖 12 种常见输入（每个 action 至少 2 例正例 + 1 例反例）
- ``classify("跑下登录用例")`` → execute_test, conf≥0.85, target="登录"
- ``classify("昨天跑用例失败率")`` → query_history, conf≥0.8
- ``classify("怎么写好用例")`` → learn, conf≥0.8
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.skills.builtin.ui_automation import intent_classifier
from app.modules.skills.builtin.ui_automation.intent_classifier import (
    UI_AUTOMATION_INTENT_GUARDED,
    IntentResult,
    _rule_based_classify,
    classify,
)


@pytest.fixture(autouse=True)
def _isolate_cache() -> None:
    intent_classifier._cache_clear_for_test()


# ────────────────── DoD 三条样例 ──────────────────


def test_dod_execute_test_basic() -> None:
    r = _rule_based_classify("跑下登录用例")
    assert r.action == "execute_test"
    assert r.confidence >= 0.85
    assert r.target == "登录"


def test_dod_query_history_yesterday() -> None:
    r = _rule_based_classify("昨天跑用例的失败率多少")
    assert r.action == "query_history"
    assert r.confidence >= 0.8


def test_dod_learn_how_to_write() -> None:
    r = _rule_based_classify("怎么写好登录用例")
    assert r.action == "learn"
    assert r.confidence >= 0.8


# ────────────────── 12+ 种输入：每 action ≥ 2 正例 + 1 反例 ──────────────────


@pytest.mark.parametrize(
    "msg, target",
    [
        ("跑下登录用例", "登录"),
        ("帮我跑结算流程", "结算"),
        ("执行 #123", "#123"),
        ("run TC-007", "TC-007"),
    ],
)
def test_execute_test_positives(msg: str, target: str) -> None:
    r = _rule_based_classify(msg)
    assert r.action == "execute_test"
    assert r.confidence >= 0.85
    assert r.target == target


def test_execute_test_negative_only_noun() -> None:
    """裸名词无动作动词不应被高置信判执行。"""
    r = _rule_based_classify("登录用例")
    assert r.action != "execute_test" or r.confidence < 0.7


@pytest.mark.parametrize(
    "msg",
    [
        "昨天跑用例的失败率多少",
        "上次跑了几条",
        "看看通过率",
        "最近一次的执行历史",
    ],
)
def test_query_history_positives(msg: str) -> None:
    r = _rule_based_classify(msg)
    assert r.action == "query_history"
    assert r.confidence >= 0.85


def test_query_history_negative_pure_execute() -> None:
    r = _rule_based_classify("跑下 #123")
    assert r.action != "query_history"


@pytest.mark.parametrize(
    "msg",
    [
        "怎么写好用例",
        "如何入门 UI 自动化",
        "什么是冒烟测试",
        "教我写断言",
    ],
)
def test_learn_positives(msg: str) -> None:
    r = _rule_based_classify(msg)
    assert r.action == "learn"
    assert r.confidence >= 0.8


def test_learn_negative_execute_verb() -> None:
    r = _rule_based_classify("帮我跑下登录用例")
    assert r.action != "learn"


@pytest.mark.parametrize(
    "msg",
    [
        "把登录用例的预期改成首页",
        "修改一下用例步骤",
        "调整下断言",
    ],
)
def test_edit_testcase_positives(msg: str) -> None:
    r = _rule_based_classify(msg)
    assert r.action == "edit_testcase"
    assert r.confidence >= 0.8


def test_edit_testcase_negative_query() -> None:
    r = _rule_based_classify("看看用例的失败率")
    assert r.action != "edit_testcase"


@pytest.mark.parametrize(
    "msg",
    [
        "今天上海天气怎么样",
        "你叫什么名字",
        "",
    ],
)
def test_other_fallback(msg: str) -> None:
    r = _rule_based_classify(msg)
    assert r.action != "execute_test" or r.confidence < 0.7


# ────────────────── 缓存：同 session+message 60s 内只跑一次 ──────────────────


@pytest.mark.asyncio
async def test_cache_avoids_duplicate_classification() -> None:
    sid = uuid.uuid4()
    calls: list[str] = []

    async def fake_llm(_prompt: str) -> str:
        calls.append("hit")
        return '{"action":"other","target":null,"confidence":0.5}'

    # 第一次跑（layer1 conf=0.4 触发 layer2）
    r1 = await classify("今天上海天气", session_id=sid, llm_classifier=fake_llm)
    # 第二次同 message 同 session：直接命中缓存
    r2 = await classify("今天上海天气", session_id=sid, llm_classifier=fake_llm)
    assert r1 == r2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_classify_high_layer1_skips_layer2() -> None:
    sid = uuid.uuid4()
    layer2_called = False

    async def fake_llm(_p: str) -> str:
        nonlocal layer2_called
        layer2_called = True
        return '{"action":"other","target":null,"confidence":1.0}'

    r = await classify("跑下登录用例", session_id=sid, llm_classifier=fake_llm)
    assert r.action == "execute_test"
    assert layer2_called is False


@pytest.mark.asyncio
async def test_layer2_takes_over_when_layer1_low() -> None:
    sid = uuid.uuid4()

    async def fake_llm(_p: str) -> str:
        return '{"action":"execute_test","target":"登录","confidence":0.92}'

    r = await classify("登录用例", session_id=sid, llm_classifier=fake_llm)
    assert r.action == "execute_test"
    assert r.confidence >= 0.85


@pytest.mark.asyncio
async def test_layer2_invalid_json_falls_back_safely() -> None:
    async def fake_llm(_p: str) -> str:
        return "not json at all"

    # 选 layer1 conf=0.55（execute_verb 命中但 target 模糊）让 layer2 兑底；
    # layer2 解析失败返回低置信结果，按 final = layer2 if layer2.conf >=
    # rule.conf else rule 应保留 layer1 的"execute_test"判定。
    r = await classify("帮我跑下", session_id=None, llm_classifier=fake_llm)
    assert r.action == "execute_test"


@pytest.mark.asyncio
async def test_layer2_timeout_does_not_raise() -> None:
    import asyncio as _asyncio

    async def slow_llm(_p: str) -> str:
        await _asyncio.sleep(15.0)
        return "ignored"

    # 直接调 _llm_based_classify 走 timeout 分支，避免依赖整个 classify cache 状态。
    r = await intent_classifier._llm_based_classify(
        "yo", llm_classifier=slow_llm,
    )
    assert isinstance(r, IntentResult)
    assert r.action == "other"


def test_guarded_set_contains_default() -> None:
    assert "system_ui_automation" in UI_AUTOMATION_INTENT_GUARDED
