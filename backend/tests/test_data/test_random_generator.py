"""``random_generator.generate`` 单测。

覆盖：
- 每种模板的格式（长度 / 字符集 / 前后缀）
- N 参数的边界（超上限截断、非法回退 default）
- 未识别模板的 fail-open 行为
- 随机性：连跑 20 次不得全部一致
"""

from __future__ import annotations

import re

import pytest

from app.modules.test_data.random_generator import (
    SUPPORTED_TEMPLATES,
    generate,
)

# ─── phone ────────────────────────────────────────────────────────────


def test_phone_cn_format() -> None:
    for _ in range(10):
        v = generate("phone:CN")
        assert re.fullmatch(r"1[3-9]\d{9}", v), v


def test_phone_default_is_cn() -> None:
    # "phone" 没带参数 → 等同 "phone:CN"
    v = generate("phone")
    assert re.fullmatch(r"1[3-9]\d{9}", v)


def test_phone_us_format() -> None:
    v = generate("phone:US")
    assert re.fullmatch(r"1\d{10}", v), v


def test_phone_unknown_region_falls_back_to_cn() -> None:
    v = generate("phone:UK")
    assert re.fullmatch(r"1[3-9]\d{9}", v), v


# ─── email ────────────────────────────────────────────────────────────


def test_email_default_domain() -> None:
    v = generate("email")
    assert v.endswith("@example.com")
    prefix, _, _ = v.partition("@")
    assert prefix


def test_email_custom_domain() -> None:
    v = generate("email:gmail.com")
    assert v.endswith("@gmail.com")


# ─── uuid ─────────────────────────────────────────────────────────────


def test_uuid_format() -> None:
    v = generate("uuid")
    # 8-4-4-4-12 格式
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v), v


def test_uuid4_alias_same_as_uuid() -> None:
    # 两个模板都能跑
    v = generate("uuid4")
    assert re.fullmatch(r"[0-9a-f-]{36}", v)


# ─── digits ───────────────────────────────────────────────────────────


def test_digits_default_length() -> None:
    v = generate("digits")
    assert len(v) == 8
    assert v.isdigit()


def test_digits_explicit_length() -> None:
    v = generate("digits:16")
    assert len(v) == 16
    assert v.isdigit()


def test_digits_invalid_n_falls_back() -> None:
    v = generate("digits:abc")
    assert len(v) == 8  # 解析失败退回 default


def test_digits_exceeds_cap() -> None:
    v = generate("digits:9999")
    # 上限 128
    assert len(v) == 128


def test_digits_negative_clamped_to_one() -> None:
    v = generate("digits:-5")
    assert len(v) == 1


# ─── hex / letters / alnum ───────────────────────────────────────────


def test_hex_default_length() -> None:
    v = generate("hex")
    assert re.fullmatch(r"[0-9a-f]{16}", v), v


def test_hex_explicit_length() -> None:
    v = generate("hex:8")
    assert re.fullmatch(r"[0-9a-f]{8}", v)


def test_letters() -> None:
    v = generate("letters:10")
    assert len(v) == 10
    assert v.isalpha()


def test_alnum() -> None:
    v = generate("alnum:12")
    assert len(v) == 12
    assert v.isalnum()


# ─── username / name ─────────────────────────────────────────────────


def test_username_starts_with_letter() -> None:
    v = generate("username")
    assert v[0].isalpha() and v[0].islower()
    # 默认 6..10 位
    assert 6 <= len(v) <= 10


def test_username_explicit_length() -> None:
    v = generate("username:12")
    assert len(v) == 12
    assert v[0].isalpha()


def test_name_is_alias_of_username() -> None:
    v = generate("name")
    assert 6 <= len(v) <= 10
    assert v[0].isalpha()


# ─── timestamp ────────────────────────────────────────────────────────


def test_timestamp_looks_like_ms_epoch() -> None:
    v = generate("timestamp")
    assert v.isdigit()
    # 毫秒级应在 10..16 位区间（≥ 2001 年）
    assert len(v) >= 10


# ─── fail-open 行为 ───────────────────────────────────────────────────


def test_unknown_template_returns_original() -> None:
    assert generate("nonexistent:xxx") == "nonexistent:xxx"
    assert generate("") == ""
    assert generate("gibberish") == "gibberish"


def test_template_with_whitespace_is_stripped() -> None:
    v = generate("  digits:4  ")
    assert len(v) == 4
    assert v.isdigit()


# ─── 随机性 sanity check ─────────────────────────────────────────────


def test_repeated_calls_not_all_identical() -> None:
    # 20 次 phone 生成至少要 > 5 种不同结果（几乎 100% 概率）
    seen = {generate("phone:CN") for _ in range(20)}
    assert len(seen) > 5


def test_supported_templates_non_empty() -> None:
    assert "phone" in SUPPORTED_TEMPLATES
    assert "uuid" in SUPPORTED_TEMPLATES
    assert "digits" in SUPPORTED_TEMPLATES


@pytest.mark.parametrize("tpl", ["phone:CN", "email", "uuid", "digits:6", "hex:16"])
def test_each_template_produces_non_empty_string(tpl: str) -> None:
    v = generate(tpl)
    assert isinstance(v, str)
    assert v != ""
