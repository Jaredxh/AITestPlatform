"""Task 8.1 验证：service 层的纯计算 helper（不需要 DB）。

DB 涉及的 CRUD 路径靠人工 / 后续 e2e 测试覆盖；本文件只测：
- ``extract_host_from_url`` 各种 URL 形态
- 凭据加密 / 解密 roundtrip（含 ``reveal_credentials``）
- ``schemas`` 与 ``models.PRECONDITION_TYPES`` 的常量同步性
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.modules.ui_automation.models import PRECONDITION_TYPES
from app.modules.ui_automation.schemas import (
    PRECONDITION_TYPE_PATTERN,
    PreconditionTemplateCreateRequest,
    TestEnvironmentCreateRequest,
)
from app.modules.ui_automation.service import (
    _encrypt_credentials,
    extract_host_from_url,
    reveal_credentials,
)

# ─── extract_host_from_url ────────────────────────────────────────────


@pytest.mark.parametrize("url, expected", [
    ("https://staging.foo.com/login", "staging.foo.com"),
    ("https://staging.foo.com", "staging.foo.com"),
    ("http://localhost", "localhost"),
    ("http://localhost:8080", "localhost:8080"),
    ("http://localhost:80", "localhost"),         # 默认端口被省略
    ("https://staging.foo.com:443", "staging.foo.com"),
    ("https://STAGING.FOO.COM/x", "staging.foo.com"),  # 大小写归一
    ("https://user:pwd@staging.foo.com/x", "staging.foo.com"),  # userinfo 剥掉
])
def test_extract_host_from_url_variants(url: str, expected: str) -> None:
    assert extract_host_from_url(url) == expected


@pytest.mark.parametrize("url", ["", None, "not a url at all", "javascript:alert(1)"])
def test_extract_host_from_url_returns_none_for_garbage(url) -> None:
    assert extract_host_from_url(url) is None


# ─── 凭据加密 roundtrip ──────────────────────────────────────────────


def test_encrypt_credentials_returns_none_for_empty() -> None:
    assert _encrypt_credentials(None) is None
    assert _encrypt_credentials({}) is None


def test_encrypt_credentials_produces_decryptable_string() -> None:
    creds = {"username": "admin", "password": "p@ss·中文·123"}
    enc = _encrypt_credentials(creds)
    assert enc is not None
    assert enc != json.dumps(creds)  # 必须真的加密了
    # 反向 reveal
    pt_stub = SimpleNamespace(credentials_encrypted=enc, id=uuid.uuid4())
    decoded = reveal_credentials(pt_stub)  # type: ignore[arg-type]
    assert decoded == creds


def test_reveal_credentials_returns_none_when_empty() -> None:
    pt_stub = SimpleNamespace(credentials_encrypted=None, id=uuid.uuid4())
    assert reveal_credentials(pt_stub) is None  # type: ignore[arg-type]
    pt_stub = SimpleNamespace(credentials_encrypted="", id=uuid.uuid4())
    assert reveal_credentials(pt_stub) is None  # type: ignore[arg-type]


def test_reveal_credentials_raises_on_corrupt_ciphertext() -> None:
    """ENCRYPT_KEY 不一致 / 数据损坏时必须抛 AppException 而非默默返回 None。"""
    from app.core.exceptions import AppException

    pt_stub = SimpleNamespace(credentials_encrypted="not-a-real-fernet-token", id=uuid.uuid4())
    with pytest.raises(AppException):
        reveal_credentials(pt_stub)  # type: ignore[arg-type]


# ─── schemas vs models 常量同步 ───────────────────────────────────────


def test_precondition_pattern_matches_all_model_types() -> None:
    """PRECONDITION_TYPE_PATTERN 必须 1:1 覆盖 PRECONDITION_TYPES。"""
    import re

    pattern = re.compile(PRECONDITION_TYPE_PATTERN)
    for t in PRECONDITION_TYPES:
        assert pattern.fullmatch(t), f"pattern 漏了 type={t}"
    # 反向：pattern 不应放过别的 type
    assert not pattern.fullmatch("evil_type")


# ─── pydantic schema 行为 ────────────────────────────────────────────


def test_create_env_request_strips_and_lowercases_hosts() -> None:
    req = TestEnvironmentCreateRequest(
        name="dev",
        base_url="https://dev.foo.com",
        allowed_hosts=["  Dev.Foo.Com  ", "", "  *.bar.com  "],
    )
    assert req.allowed_hosts == ["dev.foo.com", "*.bar.com"]


def test_create_env_request_rejects_invalid_token_budget() -> None:
    with pytest.raises(Exception):
        TestEnvironmentCreateRequest(
            name="dev",
            base_url="https://dev.foo.com",
            token_budget=10,  # 低于下限 1000
        )


def test_create_env_request_rejects_non_http_base_url() -> None:
    """base_url 必须是 http/https；file:// / 自定义 scheme 拒。"""
    with pytest.raises(Exception):
        TestEnvironmentCreateRequest(
            name="dev",
            base_url="ftp://staging.foo.com",  # AnyHttpUrl 拒非 http(s)
        )


def test_create_precondition_validates_type() -> None:
    PreconditionTemplateCreateRequest(name="x", type="ai_login")
    with pytest.raises(Exception):
        PreconditionTemplateCreateRequest(name="x", type="evil_type")


def test_create_precondition_credentials_optional() -> None:
    req = PreconditionTemplateCreateRequest(name="x", type="state_inject")
    assert req.credentials is None
    assert req.config == {}
