"""SSE 编码工具 — UI 自动化 SSE 端点专用。

与一期 ``chat_service`` 的 SSE 协议**完全一致**：
- 每条事件序列化为 ``data: <json>\\n\\n``
- ``<json>`` 是一个对象，至少含 ``"type"`` 字段标识事件类别
- 前端用同一套 ``useSSE.ts`` composable 解析；UI 自动化只是事件 ``type`` 集
  更丰富一些（含 case_start / step_progress / data_synthesized 等）

设计哲学：
- 只导出**少量**通用 helper（``sse_event`` + 三个最常用的）；不为每种事件
  类型写一个 helper，否则会有 20+ 个几乎相同的函数，反而冗余。
- 调用方需要发某个特定类型时，直接 ``sse_event("step_progress", ...)``，
  完全等价但语义清晰。
- 与 chat hub 集成时（``_handle_ui_test_intent`` 转发 SSE 事件到 chat 流），
  ``parse_sse_chunk`` 用于反向解析。
"""

from __future__ import annotations

import json
from typing import Any


def sse_encode(event: dict) -> str:
    """把 dict 编码成 ``data: <json>\\n\\n`` 格式的 SSE 帧。

    用 ``ensure_ascii=False`` 保留中文原样，避免前端再做一遍 unescape；
    这一点和一期 chat 的 ``_sse`` 完全一致。
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_event(event_type: str, **payload: Any) -> str:
    """通用事件构造器。

    示例：
        sse_event("step_progress", case_id="...", step=1, action="点击登录")
        sse_event("data_synthesized", key="captcha", value_preview="0000",
                  source="heuristic_exact", case_id="...", step_id="...")
    """
    return sse_encode({"type": event_type, **payload})


# ─────────────── 极少数高频 helper ───────────────
# 只为最频繁出现的三种类型提供 helper，以减少调用方拼写错误（"info" 写成
# "infor" 这种）。其他类型一律走 sse_event。


def sse_info(message: str) -> str:
    """状态/提示信息（联网中、生成中等）。等价 chat 的 ``_sse_info``。"""
    return sse_encode({"type": "info", "message": message})


def sse_error(message: str) -> str:
    """错误信息。前端会用 toast / 红色文本展示。"""
    return sse_encode({"type": "error", "message": message})


def sse_done(payload: dict | None = None) -> str:
    """流结束信号。前端收到后会关闭 EventSource。

    可附带 payload（典型用 ``{"execution_id": "..."}`` 让前端确认是哪个
    execution 完成了）。
    """
    event: dict[str, Any] = {"type": "done"}
    if payload:
        event.update(payload)
    return sse_encode(event)


def parse_sse_chunk(chunk: str | bytes) -> dict | None:
    """把 ``data: <json>\\n\\n`` 字节/字符串解回 dict。

    用于 chat orchestrator 模式下"再次订阅 + 转发"场景：UI 测试意图被 chat
    捕获后，``_handle_ui_test_intent`` 会读取 ExecutionStreamHub 的事件流，
    经过这个反解析后再以 chat SSE 协议吐到对话流里。

    解析失败（非 SSE / 非 JSON）返回 None，调用方应跳过。
    """
    if isinstance(chunk, (bytes, bytearray)):
        chunk = chunk.decode("utf-8", errors="ignore")
    line = chunk.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    try:
        result = json.loads(payload)
        if isinstance(result, dict):
            return result
        return None
    except json.JSONDecodeError:
        return None
