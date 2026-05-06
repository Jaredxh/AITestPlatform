"""``_resolve_target_url`` 单测——验证 module entry_path → 目标 URL 的拼接规则。

不连 DB / Engine / StepRunner；只测纯函数。覆盖：
1. override 优先级高于 module.entry_path
2. 完整 URL（http/https）原样使用，不拼 base_url
3. 相对路径与 base_url 正确拼接（去重 / 加 /）
4. 空串 / None / 未配模块 → 返回 None（让 prompt 回退到现状）
5. environment 没 base_url 时相对路径只能返回 None（不能伪造一个 URL）
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.modules.ui_automation.execution_engine import _resolve_target_url


def _make_tc(module_id: uuid.UUID | None) -> SimpleNamespace:
    """构造一个最小 testcase stub。"""
    return SimpleNamespace(id=uuid.uuid4(), module_id=module_id)


def _make_env(base_url: str | None = "https://app.example.com") -> SimpleNamespace:
    return SimpleNamespace(base_url=base_url)


def test_no_module_returns_none() -> None:
    """没归模块的用例 → 没法拿 entry_path，返回 None 让 AI 回退到现状。"""
    tc = _make_tc(module_id=None)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env(),
        module_entry_map={},
        module_entry_overrides={},
    )
    assert out is None


def test_module_no_entry_returns_none() -> None:
    """模块存在但没配 entry_path → None，行为退回现状。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env(),
        module_entry_map={mid: None},
        module_entry_overrides={},
    )
    assert out is None


def test_relative_entry_path_joins_base_url() -> None:
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "/admin/users"},
        module_entry_overrides={},
    )
    assert out == "https://app.example.com/admin/users"


def test_relative_entry_path_normalizes_slashes() -> None:
    """``base_url`` 末尾带 / + entry_path 头部带 / 不能拼出双斜杠。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com/"),
        module_entry_map={mid: "/admin/users"},
        module_entry_overrides={},
    )
    assert out == "https://app.example.com/admin/users"


def test_full_url_entry_path_used_verbatim() -> None:
    """完整 URL 跨子域使用——不拼 base_url。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "https://other.example.com/dashboard"},
        module_entry_overrides={},
    )
    assert out == "https://other.example.com/dashboard"


def test_override_takes_priority_over_module_entry() -> None:
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "/admin/users"},
        module_entry_overrides={mid: "/settings/profile"},
    )
    assert out == "https://app.example.com/settings/profile"


def test_override_with_empty_string_clears_entry() -> None:
    """显式覆盖空串 = '本次跑该模块时不带 entry_path'，等价于 None。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "/admin/users"},
        module_entry_overrides={mid: ""},
    )
    assert out is None


def test_override_full_url_used_verbatim() -> None:
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "/x"},
        module_entry_overrides={mid: "https://canary.example.com/y"},
    )
    assert out == "https://canary.example.com/y"


def test_relative_entry_path_without_leading_slash() -> None:
    """``entry_path = 'admin/users'`` 也能正常拼，不要求一定有 leading /。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env("https://app.example.com"),
        module_entry_map={mid: "admin/users"},
        module_entry_overrides={},
    )
    assert out == "https://app.example.com/admin/users"


def test_empty_base_url_with_relative_path_returns_none() -> None:
    """环境 base_url 空，相对路径拼不出 URL → 返回 None 而不是垃圾值。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env(base_url=""),
        module_entry_map={mid: "/admin/users"},
        module_entry_overrides={},
    )
    assert out is None


def test_empty_base_url_but_full_url_entry_still_works() -> None:
    """即使环境没填 base_url，完整 URL 的 entry 仍可使用。"""
    mid = uuid.uuid4()
    tc = _make_tc(module_id=mid)
    out = _resolve_target_url(
        tc=tc,
        environment=_make_env(base_url=""),
        module_entry_map={mid: "https://other.example.com/x"},
        module_entry_overrides={},
    )
    assert out == "https://other.example.com/x"
