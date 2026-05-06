"""service 层纯 helper 函数的单测（不需要 DB）。

覆盖：
- ``_sanitize_filename``：路径注入 / 非法字符 / 超长
- ``_validate_file_upload``：空 / 超大 / 扩展名黑名单 / MIME 白名单
- ``_ensure_user_can_reveal``：三条放行路径
- schemas 的 key 正则校验
- Fernet 加解密 roundtrip（借用 ``encrypt`` / ``decrypt`` 模块级函数）
- 常量 sanity：``VALUE_TYPES`` / ``SCOPES`` 与 schemas pattern 一致
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest

from app.core.crypto import decrypt, encrypt
from app.core.exceptions import AppException, PermissionDeniedException
from app.modules.auth.permissions import Permissions
from app.modules.test_data.models import SCOPES, VALUE_TYPES
from app.modules.test_data.schemas import (
    SCOPE_PATTERN,
    VALUE_TYPE_PATTERN,
    TestDataItemCreateRequest,
    TestDataSetCreateRequest,
)
from app.modules.test_data.service import (
    _BLOCKED_EXTENSIONS,
    _ensure_user_can_reveal,
    _sanitize_filename,
    _validate_file_upload,
)

# ─── _sanitize_filename ──────────────────────────────────────────────


def test_sanitize_strips_path_separators() -> None:
    assert _sanitize_filename("../../etc/passwd") == "passwd"
    assert _sanitize_filename("/abs/path/to/file.txt") == "file.txt"


def test_sanitize_keeps_cjk() -> None:
    assert _sanitize_filename("测试文件.png") == "测试文件.png"


def test_sanitize_replaces_specials() -> None:
    out = _sanitize_filename("weird name@$%.jpg")
    # 空格 / @ / $ / % 都该被 _ 替换
    assert "_" in out
    assert out.endswith(".jpg")


def test_sanitize_empty_falls_back() -> None:
    assert _sanitize_filename("") == "unknown"
    assert _sanitize_filename("   ") == "unknown"


def test_sanitize_length_cap() -> None:
    long_name = "a" * 300 + ".txt"
    out = _sanitize_filename(long_name)
    assert len(out) <= 100
    assert out.endswith(".txt")


# ─── _validate_file_upload ────────────────────────────────────────────


def test_validate_rejects_empty() -> None:
    with pytest.raises(AppException) as ei:
        _validate_file_upload("f.txt", "text/plain", 0)
    assert ei.value.code == "EMPTY_FILE"


def test_validate_rejects_too_large() -> None:
    # 设置假设最大 50MB；我们传 200MB 必拒
    with pytest.raises(AppException) as ei:
        _validate_file_upload("big.zip", "application/zip", 200 * 1024 * 1024)
    assert ei.value.code == "FILE_TOO_LARGE"


@pytest.mark.parametrize("ext", [".exe", ".bat", ".sh", ".scr", ".dll"])
def test_validate_rejects_blocked_extensions(ext: str) -> None:
    with pytest.raises(AppException) as ei:
        _validate_file_upload(f"malware{ext}", "application/octet-stream", 1024)
    assert ei.value.code == "BLOCKED_FILE_EXTENSION"


def test_validate_accepts_png_image() -> None:
    _validate_file_upload("logo.png", "image/png", 500 * 1024)


def test_validate_accepts_csv() -> None:
    _validate_file_upload("data.csv", "text/csv", 2 * 1024 * 1024)


def test_validate_accepts_pdf() -> None:
    _validate_file_upload("spec.pdf", "application/pdf", 1_000_000)


def test_validate_accepts_xlsx() -> None:
    _validate_file_upload(
        "rpt.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        100_000,
    )


def test_validate_rejects_unknown_mime() -> None:
    with pytest.raises(AppException) as ei:
        _validate_file_upload("weird.xyz", "application/x-mystery", 1024)
    assert ei.value.code == "UNSUPPORTED_MIME"


def test_validate_accepts_missing_mime_with_safe_extension() -> None:
    # 没给 mime 时只靠扩展名；扩展名不在黑名单就放行
    _validate_file_upload("notes.txt", "", 100)


def test_blocked_extensions_cover_common_bad_types() -> None:
    # 保证上面几个参数化测试之外常用的也在 list 里
    for ext in (".exe", ".msi", ".scr", ".sh", ".ps1", ".jar", ".vbs"):
        assert ext in _BLOCKED_EXTENSIONS


# ─── _ensure_user_can_reveal ──────────────────────────────────────────


def _fake_user(
    *,
    is_superuser: bool = False,
    has_reveal: bool = False,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    uid = user_id or uuid.uuid4()
    def _has_permission(p: str) -> bool:
        return has_reveal and p == Permissions.TEST_DATA_REVEAL
    return SimpleNamespace(
        id=uid, is_superuser=is_superuser, has_permission=_has_permission,
    )


def _fake_set(*, scope: str = "project", owner_id: uuid.UUID | None = None):
    return SimpleNamespace(scope=scope, owner_id=owner_id)


def test_reveal_allowed_for_superuser() -> None:
    user = _fake_user(is_superuser=True)
    _ensure_user_can_reveal(SimpleNamespace(), _fake_set(), user)


def test_reveal_allowed_with_permission() -> None:
    user = _fake_user(has_reveal=True)
    _ensure_user_can_reveal(SimpleNamespace(), _fake_set(), user)


def test_reveal_allowed_for_personal_owner() -> None:
    uid = uuid.uuid4()
    user = _fake_user(user_id=uid)
    ds = _fake_set(scope="personal", owner_id=uid)
    _ensure_user_can_reveal(SimpleNamespace(), ds, user)


def test_reveal_blocked_for_stranger_on_personal() -> None:
    user = _fake_user(user_id=uuid.uuid4())
    ds = _fake_set(scope="personal", owner_id=uuid.uuid4())
    with pytest.raises(PermissionDeniedException):
        _ensure_user_can_reveal(SimpleNamespace(), ds, user)


def test_reveal_blocked_for_project_without_permission() -> None:
    user = _fake_user()  # 普通成员，没有 reveal 权限
    ds = _fake_set(scope="project")
    with pytest.raises(PermissionDeniedException):
        _ensure_user_can_reveal(SimpleNamespace(), ds, user)


# ─── schemas 格式校验 ────────────────────────────────────────────────


def test_key_regex_rejects_invalid() -> None:
    bad_keys = ["1username", "user-name", "user.name", "用户名", " space", ""]
    for k in bad_keys:
        with pytest.raises(Exception):
            TestDataItemCreateRequest(key=k, value_type="string")


def test_key_regex_accepts_valid() -> None:
    ok = ["username", "user_name", "user1", "U"]
    for k in ok:
        TestDataItemCreateRequest(key=k, value_type="string")


def test_set_scope_pattern() -> None:
    TestDataSetCreateRequest(name="x", scope="project")
    TestDataSetCreateRequest(name="x", scope="environment", environment_id=uuid.uuid4())
    TestDataSetCreateRequest(name="x", scope="personal")
    with pytest.raises(Exception):
        TestDataSetCreateRequest(name="x", scope="wrong")


def test_value_type_pattern() -> None:
    for t in VALUE_TYPES:
        TestDataItemCreateRequest(key="k", value_type=t)
    with pytest.raises(Exception):
        TestDataItemCreateRequest(key="k", value_type="unknown")


# ─── Fernet roundtrip ────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip() -> None:
    for secret in ("hello", "p@ssw0rd!", "中文密码 🔑", "a" * 200):
        enc = encrypt(secret)
        assert enc != secret  # 加密后肯定变形
        assert decrypt(enc) == secret


# ─── 常量同步 sanity ─────────────────────────────────────────────────


def test_value_types_and_pattern_in_sync() -> None:
    regex = re.compile(VALUE_TYPE_PATTERN)
    for t in VALUE_TYPES:
        assert regex.fullmatch(t), t


def test_scopes_and_pattern_in_sync() -> None:
    regex = re.compile(SCOPE_PATTERN)
    for s in SCOPES:
        assert regex.fullmatch(s), s


def test_expected_value_type_list() -> None:
    # 明确断言 6 种；未来加 type 必须更新 this test（提醒同步文档）
    assert set(VALUE_TYPES) == {
        "string", "secret", "multiline", "file", "random", "dataset",
    }


def test_expected_scope_list() -> None:
    assert set(SCOPES) == {"project", "environment", "personal"}
