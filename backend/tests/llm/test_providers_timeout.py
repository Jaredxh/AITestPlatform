"""Regression: ``stream_chat`` 必须给 LLM 客户端注入显式 idle timeout +
关闭 SDK 自动重试。

历史故障：火山方舟/智谱等网关在 thinking + tool-calling 多轮场景里偶尔
"开了 SSE 连接、吐了几个 token 就停"——OpenAI SDK 默认 600s read timeout
让用户看到的现象是「输出了几个字之后永远卡住」，而且后台任务跟着占着 DB
连接 + chat_stream hub。这里固定 read=45s，把那种半开流转成上层可见的
ReadTimeout。
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.llm import providers


def test_build_client_uses_explicit_idle_timeout_and_no_retries(monkeypatch):
    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(providers, "AsyncOpenAI", _StubClient)

    providers.build_client("openai", api_key="sk-test")

    timeout = captured.get("timeout")
    assert isinstance(timeout, httpx.Timeout), (
        "stream_chat must hand a httpx.Timeout to AsyncOpenAI; without it "
        "the SDK silently uses 600s and the chat appears stuck for ~10min "
        "when a gateway pauses the SSE stream"
    )
    # httpx.Timeout API: ``read`` is the **chunk-to-chunk** idle, exactly what
    # we want bounded for "model started, then stalls". 45s is the documented
    # contract; tightening it would clip slow-but-healthy thinking models.
    assert timeout.read == pytest.approx(45.0)
    assert timeout.connect == pytest.approx(10.0)

    assert captured.get("max_retries") == 0, (
        "SDK auto-retry doubles user-visible latency on 5xx/idle and clobbers "
        "_handle_chat_stream's per-round error UX; keep retries off"
    )


def test_build_client_still_propagates_base_url_and_key(monkeypatch):
    captured: dict = {}

    class _StubClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(providers, "AsyncOpenAI", _StubClient)

    providers.build_client(
        "deepseek",
        api_key="abc",
        base_url="https://example.test/v1",
    )

    assert captured["api_key"] == "abc"
    assert captured["base_url"] == "https://example.test/v1"
