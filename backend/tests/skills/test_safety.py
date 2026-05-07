"""Task 12.2 — safety helpers."""

from app.modules.skills.safety import HTTP_TOOL_HINT, extract_when_to_use, wrap_with_safety


def test_wrap_with_safety_prefix() -> None:
    out = wrap_with_safety("hello")
    assert "hello" in out
    assert out.startswith("【技能包安全提示】")


def test_extract_when_to_use_section() -> None:
    body = "# X\n\n## 何时使用\n仅测试场景。\n\n## 其它\nno"
    assert "仅测试场景" in extract_when_to_use(body)


def test_wrap_with_safety_appends_http_hint_when_body_has_urls() -> None:
    body = "调用 GET http://172.17.208.45:5004/api/x 获取数据。"
    out = wrap_with_safety(body)
    assert HTTP_TOOL_HINT.strip() in out
    assert "http_get_json" in out


def test_wrap_with_safety_skips_http_hint_when_no_urls() -> None:
    body = "纯文本指南，没有任何 URL。"
    out = wrap_with_safety(body)
    assert HTTP_TOOL_HINT.strip() not in out
    assert "http_get_json" not in out
