"""Task 11.1 — UI 统计纯函数单测。

只测可以脱离 DB 验证的纯函数：
- ``compute_pass_rate``：边界 / 排除 data_failure 的语义
- ``aggregate_top_synthesized_keys``：截断 + 稳定排序
- ``aggregate_top_keys_from_python``：从 case 维度数组聚合 + 跳过非法记录

DB 聚合（``get_project_ui_stats`` / ``_query_top_synthesized_keys``）走真实 PG
路径，留给集成测试 / 烟雾验证（部署后用 curl 打）。这些纯函数承担了大部分
业务规则（双视图分母 / Top 10 排序口径），单测覆盖到这里就足够回归保护。
"""

from __future__ import annotations

import pytest

from app.modules.dashboard.ui_stats import (
    aggregate_top_keys_from_python,
    aggregate_top_synthesized_keys,
    compute_pass_rate,
)

# ─── compute_pass_rate ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("passed", "denom", "expected"),
    [
        (0, 0, 0.0),       # 分母 0 不抛 ZeroDivisionError，返回 0
        (10, 10, 100.0),   # 全过
        (0, 10, 0.0),      # 全挂
        (3, 10, 30.0),
        (1, 3, 33.33),     # 两位小数
        (5, 0, 0.0),       # 病态：passed > 0 且 denom 0（业务上理论不会，护栏）
    ],
)
def test_compute_pass_rate(passed: int, denom: int, expected: float) -> None:
    assert compute_pass_rate(passed, denom) == expected


def test_compute_pass_rate_business_excludes_data_failure() -> None:
    """业务通过率的核心语义：分母 = total - data_failure。

    场景：10 个 case，6 通过、2 失败、2 缺料失败。
    - 执行通过率 = 6/10 = 60%
    - 业务通过率 = 6/(10-2) = 75%（缺料的不算质量信号）
    """
    total = 10
    passed = 6
    data_failure = 2
    business_total = max(0, total - data_failure)
    assert compute_pass_rate(passed, total) == 60.0
    assert compute_pass_rate(passed, business_total) == 75.0


def test_compute_pass_rate_business_total_clamped_to_zero() -> None:
    """极端：所有 case 都是 data_failure。业务分母 0，不应负数也不应崩。"""
    assert compute_pass_rate(0, max(0, 5 - 5)) == 0.0


# ─── aggregate_top_synthesized_keys ──────────────────────────────────


def test_aggregate_top_keys_basic_order() -> None:
    rows = [("password", 9), ("captcha", 5), ("user_email", 12)]
    out = aggregate_top_synthesized_keys(rows, limit=10)
    assert out == [
        {"key": "user_email", "count": 12},
        {"key": "password", "count": 9},
        {"key": "captcha", "count": 5},
    ]


def test_aggregate_top_keys_truncates_to_limit() -> None:
    rows = [(f"key{i}", i) for i in range(20)]
    out = aggregate_top_synthesized_keys(rows, limit=3)
    assert len(out) == 3
    assert out[0]["key"] == "key19"
    assert out[2]["key"] == "key17"


def test_aggregate_top_keys_ties_broken_by_key_ascending() -> None:
    """count 相同时按 key 升序，让前端展示稳定。"""
    rows = [("zebra", 5), ("alpha", 5), ("middle", 5)]
    out = aggregate_top_synthesized_keys(rows, limit=10)
    assert [r["key"] for r in out] == ["alpha", "middle", "zebra"]


def test_aggregate_top_keys_empty_input() -> None:
    assert aggregate_top_synthesized_keys([], limit=10) == []


# ─── aggregate_top_keys_from_python (fallback path) ──────────────────


def test_python_fallback_counts_across_cases() -> None:
    cases = [
        [{"key": "password", "value": "<secret>"}, {"key": "user_email"}],
        [{"key": "password"}],
        [{"key": "captcha"}, {"key": "password"}, {"key": "user_email"}],
    ]
    out = aggregate_top_keys_from_python(cases, limit=10)
    assert out == [
        {"key": "password", "count": 3},
        {"key": "user_email", "count": 2},
        {"key": "captcha", "count": 1},
    ]


def test_python_fallback_skips_malformed_records() -> None:
    """脏数据兜底：列表里混着 None / 非 dict / 没 key 的项；不应抛异常。"""
    cases = [
        None,                     # type: ignore[list-item]
        [],
        [{"key": "ok"}, "not-a-dict"],  # type: ignore[list-item]
        [{"key": ""}],            # 空 key 跳过
        [{"value": "no-key"}],    # 缺 key 跳过
        [{"key": 123}],           # type: ignore[dict-item]  非 str key 跳过
        [{"key": "ok"}],
    ]
    out = aggregate_top_keys_from_python(cases, limit=10)
    assert out == [{"key": "ok", "count": 2}]


def test_python_fallback_empty() -> None:
    assert aggregate_top_keys_from_python([], limit=10) == []
