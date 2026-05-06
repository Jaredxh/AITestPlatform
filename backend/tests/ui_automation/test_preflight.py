"""preflight + serialize_resolver_for_preview 单测（无 DB）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core import crypto
from app.modules.test_data.service import serialize_resolver_for_preview
from app.modules.ui_automation.preflight import (
    extract_template_keys,
    preflight_data_check,
)
from app.modules.ui_automation.test_data_resolver import (
    TestDataItem,
    TestDataResolver,
)


def _make_step(num: int, action: str, expected: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(step_number=num, action=action, expected_result=expected)


def _make_testcase(steps: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), steps=steps)


# ── extract_template_keys ────────────────────────────────────────────


def test_extract_keys_dedup_preserve_order() -> None:
    keys = extract_template_keys("hi {{user}} again {{user}} and {{captcha}}")
    assert keys == ["user", "captcha"]


def test_extract_keys_handles_none_and_empty() -> None:
    assert extract_template_keys(None) == []
    assert extract_template_keys("") == []


def test_extract_keys_ignores_malformed() -> None:
    assert extract_template_keys("{{ bad space }} {{ok_key}}") == ["ok_key"]


# ── preflight_data_check ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_returns_only_missing_keys() -> None:
    resolver = TestDataResolver.from_merge_dict({"user": TestDataItem.adhoc("user", "admin")})
    tc = _make_testcase([
        _make_step(1, "登录用户 {{user}}", expected="跳转首页"),
        _make_step(2, "输入验证码 {{captcha}}", expected="验证码 {{captcha}} 通过"),
    ])
    alerts = await preflight_data_check([tc], resolver)
    assert [a.key for a in alerts] == ["captcha"]
    refs = alerts[0].detected_in_steps
    assert len(refs) == 2  # action + expected
    assert {r.where for r in refs} == {"action", "expected"}
    assert refs[0].testcase_id == str(tc.id)
    assert all(a.will_synthesize for a in alerts)


@pytest.mark.asyncio
async def test_preflight_no_missing_returns_empty() -> None:
    resolver = TestDataResolver.from_merge_dict({
        "user": TestDataItem.adhoc("user", "admin"),
        "pwd": TestDataItem.adhoc("pwd", "1"),
    })
    tc = _make_testcase([_make_step(1, "登录 {{user}} {{pwd}}")])
    assert await preflight_data_check([tc], resolver) == []


@pytest.mark.asyncio
async def test_preflight_aggregates_across_testcases() -> None:
    resolver = TestDataResolver.from_merge_dict({})
    tcs = [
        _make_testcase([_make_step(1, "a {{x}}")]),
        _make_testcase([_make_step(1, "b {{x}} {{y}}")]),
    ]
    alerts = await preflight_data_check(tcs, resolver)
    assert sorted(a.key for a in alerts) == ["x", "y"]
    x_alert = next(a for a in alerts if a.key == "x")
    assert len(x_alert.detected_in_steps) == 2  # 两条用例各一次


@pytest.mark.asyncio
async def test_preflight_handles_empty_steps_gracefully() -> None:
    resolver = TestDataResolver.from_merge_dict({})
    tc = SimpleNamespace(id=uuid.uuid4(), steps=[])
    assert await preflight_data_check([tc], resolver) == []


# ── serialize_resolver_for_preview ───────────────────────────────────


def test_preview_serializer_redacts_secret_and_resolves_file_name() -> None:
    enc = crypto.encrypt("hunter2")
    data = {
        "pwd": TestDataItem(
            key="pwd",
            value_type="secret",
            value_encrypted=enc,
            description="登录密码",
        ),
        "doc": TestDataItem(
            key="doc",
            value_type="file",
            file_path="/uploads/proj/set/abc_invoice.pdf",
        ),
        "name": TestDataItem(key="name", value_type="string", value_text="ada"),
    }
    resolver = TestDataResolver.from_merge_dict(data)
    rows = serialize_resolver_for_preview(resolver)
    by_key = {r.key: r for r in rows}

    assert by_key["pwd"].display_value == "●●●●"
    assert by_key["pwd"].has_secret_value is True
    assert "hunter2" not in by_key["pwd"].model_dump_json()

    assert by_key["doc"].file_name == "abc_invoice.pdf"
    assert by_key["doc"].display_value == "abc_invoice.pdf"

    assert by_key["name"].display_value == "ada"
    assert by_key["name"].has_secret_value is False


def test_preview_serializer_marks_synthetic_source() -> None:
    resolver = TestDataResolver.from_merge_dict({})
    resolver.cache_synthesized("phone", "13800001234", "heuristic_exact")
    rows = serialize_resolver_for_preview(resolver)
    assert rows[0].synthetic_source == "heuristic_exact"
    assert rows[0].display_value == "13800001234"


def test_preview_serializer_default_sources_empty() -> None:
    """没传 ``sources_by_key`` 时每行 ``sources`` 都是空数组（向下兼容旧调用）。"""
    data = {"name": TestDataItem(key="name", value_type="string", value_text="ada")}
    resolver = TestDataResolver.from_merge_dict(data)
    rows = serialize_resolver_for_preview(resolver)
    assert rows[0].sources == []


def test_preview_serializer_attaches_sources_by_key() -> None:
    """**关键回归**：``sources_by_key`` 要按 key 注入到对应行 —— 用户验收
    反馈"多个物料集都有 username，合并明细只展示一条 username"，新字段是修
    复方案，必须保证序列化阶段不丢。"""
    import uuid as _uuid

    from app.modules.test_data.schemas import TestDataMergeSource

    data = {
        "username": TestDataItem(key="username", value_type="string", value_text="alice"),
    }
    resolver = TestDataResolver.from_merge_dict(data)

    set_a = _uuid.uuid4()
    set_b = _uuid.uuid4()
    sources = {
        "username": [
            TestDataMergeSource(
                set_id=set_a,
                set_name="项目级账号库",
                scope="project",
                display_value="bob",
                overridden=True,
            ),
            TestDataMergeSource(
                set_id=set_b,
                set_name="环境级账号库",
                scope="loaded",
                display_value="alice",
                overridden=False,
            ),
        ],
    }
    rows = serialize_resolver_for_preview(resolver, sources_by_key=sources)
    by_key = {r.key: r for r in rows}

    assert len(by_key["username"].sources) == 2
    # 顺序保持：先 project（被覆盖）→ 后 loaded（胜出）
    assert by_key["username"].sources[0].scope == "project"
    assert by_key["username"].sources[0].overridden is True
    assert by_key["username"].sources[1].scope == "loaded"
    assert by_key["username"].sources[1].overridden is False
    # 胜出值与 display_value 一致
    assert by_key["username"].display_value == "alice"
    assert by_key["username"].sources[1].display_value == "alice"
