"""Task 7.1 验证：SSE 编码 / 反解码契约。

确保编码格式与一期 chat 完全一致，从而前端能复用 useSSE.ts。
"""

from __future__ import annotations

from app.modules.ui_automation.sse import (
    parse_sse_chunk,
    sse_done,
    sse_encode,
    sse_error,
    sse_event,
    sse_info,
)


def test_sse_encode_basic_format() -> None:
    out = sse_encode({"type": "x", "v": 1})
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    assert '"type": "x"' in out
    assert '"v": 1' in out


def test_sse_encode_preserves_chinese() -> None:
    """ensure_ascii=False — 与一期 chat 一致，中文不被 \\u 转义。"""
    out = sse_encode({"type": "info", "message": "正在执行用例"})
    assert "正在执行用例" in out
    assert "\\u" not in out


def test_sse_event_helper_compose() -> None:
    out = sse_event(
        "step_progress",
        case_id="abc",
        step=1,
        action="点击登录",
    )
    assert '"type": "step_progress"' in out
    assert '"case_id": "abc"' in out
    assert '"step": 1' in out
    assert '"action": "点击登录"' in out


def test_sse_event_helper_for_data_synthesized() -> None:
    """v3.0.1 新增的物料事件应该走通用 helper。"""
    out = sse_event(
        "data_synthesized",
        key="captcha",
        value_preview="0000",
        source="heuristic_exact",
        case_id="case-1",
        step_id="step-2",
    )
    assert '"type": "data_synthesized"' in out
    assert '"source": "heuristic_exact"' in out


def test_sse_info_error_done_helpers() -> None:
    assert '"type": "info"' in sse_info("hello")
    assert '"message": "hello"' in sse_info("hello")
    assert '"type": "error"' in sse_error("oops")
    assert '"type": "done"' in sse_done()


def test_sse_done_with_payload() -> None:
    out = sse_done({"execution_id": "exec-123"})
    assert '"type": "done"' in out
    assert '"execution_id": "exec-123"' in out


def test_parse_sse_chunk_roundtrip() -> None:
    """编码 → 解码 → 拿回原 dict（保证与 chat orchestrator 集成时无损）。"""
    original = {"type": "step_complete", "case_id": "c1", "duration_ms": 1234}
    encoded = sse_encode(original)
    parsed = parse_sse_chunk(encoded)
    assert parsed == original


def test_parse_sse_chunk_handles_bytes() -> None:
    encoded = sse_encode({"type": "info", "message": "x"}).encode("utf-8")
    parsed = parse_sse_chunk(encoded)
    assert parsed == {"type": "info", "message": "x"}


def test_parse_sse_chunk_returns_none_for_garbage() -> None:
    assert parse_sse_chunk("not an sse line") is None
    assert parse_sse_chunk("data: ") is None
    assert parse_sse_chunk("data: not-json") is None
    assert parse_sse_chunk("data: [1, 2, 3]\n\n") is None  # JSON 但不是 dict
