"""Task 12 优化 — Skill 通用 HTTP 工具桥接 / 安全闸。

覆盖：
1. host 提取（裸 URL / Markdown 链接 / 默认端口归一）
2. URL 校验（白名单 / 回环 / scheme / 缺失 host）
3. run_http_get_json / run_http_post_json 实际请求（用 httpx.MockTransport）
4. 响应大小截断 / 超时
5. headers 白名单过滤
6. safe_run_tool 派发 http_*：白名单设置、清理、跨任务隔离
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.config import settings
from app.modules.skills import http_tools, safe_invoke

# ── host 提取 ─────────────────────────────────────────────


def test_extract_hosts_basic_naked_url() -> None:
    body = "调用 GET http://172.17.208.45:5004/api/x 即可。"
    assert http_tools.extract_allowed_hosts_from_body(body) == {"172.17.208.45:5004"}


def test_extract_hosts_markdown_link_and_default_port() -> None:
    body = "[文档](https://api.example.com/v1/foo) 和 http://api.example.com/v2/bar"
    out = http_tools.extract_allowed_hosts_from_body(body)
    # https 默认 443，http 默认 80
    assert out == {"api.example.com:443", "api.example.com:80"}


def test_extract_hosts_handles_multiple_protocols_and_ignores_invalid() -> None:
    body = (
        "GET http://172.17.208.45:5004/api/all\n"
        "GET http://172.17.208.45:5006/api/all?month=2026-03\n"
        "## 内部说明\n"
        "ftp://nope.example.com 不能算。\n"
    )
    out = http_tools.extract_allowed_hosts_from_body(body)
    assert out == {"172.17.208.45:5004", "172.17.208.45:5006"}


def test_extract_hosts_empty_body() -> None:
    assert http_tools.extract_allowed_hosts_from_body("") == set()


# ── URL 校验 ─────────────────────────────────────────────


def test_check_url_rejects_non_http_scheme() -> None:
    res = http_tools.check_url_against_allowed_hosts(
        "file:///etc/passwd",
        frozenset({"localhost:80"}),
    )
    assert not res.ok
    assert "http" in (res.error or "").lower()


def test_check_url_rejects_loopback_unless_in_whitelist() -> None:
    res = http_tools.check_url_against_allowed_hosts(
        "http://127.0.0.1:8000/internal",
        frozenset({"api.example.com:443"}),
    )
    assert not res.ok
    assert "回环" in (res.error or "")


def test_check_url_allows_loopback_when_in_whitelist() -> None:
    res = http_tools.check_url_against_allowed_hosts(
        "http://127.0.0.1:8000/x",
        frozenset({"127.0.0.1:8000"}),
    )
    assert res.ok


def test_check_url_rejects_when_host_not_in_whitelist() -> None:
    res = http_tools.check_url_against_allowed_hosts(
        "https://evil.example.com/exfil",
        frozenset({"api.example.com:443"}),
    )
    assert not res.ok
    assert "白名单" in (res.error or "") or "允许列表" in (res.error or "")


def test_check_url_normalizes_default_port() -> None:
    # SKILL.md 写的是 ``https://api.example.com/x``（无端口），LLM 调的也是
    # 同一 host，应该匹配上而不是因端口写法差异被拦
    res = http_tools.check_url_against_allowed_hosts(
        "https://api.example.com/v1/foo",
        frozenset({"api.example.com:443"}),
    )
    assert res.ok


# ── run_http_get_json：用 httpx.MockTransport 直接拦截，无网络 ──


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """让 ``http_tools._do_request`` 里 ``httpx.AsyncClient(...)`` 用注入的 transport。"""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.mark.asyncio
async def test_run_http_get_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"hello":"world","update_day":"2026-04-16"}',
        )

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    token = http_tools.set_active_allowed_hosts(frozenset({"api.example.com:443"}))
    try:
        out = await http_tools.run_http_get_json(
            {
                "url": "https://api.example.com/v1/data",
                "params": {"date": "2026-04-16"},
            },
        )
    finally:
        http_tools.reset_active_allowed_hosts(token)

    assert captured["method"] == "GET"
    assert "date=2026-04-16" in captured["url"]
    assert out["ok"] is True
    assert out["status_code"] == 200
    assert out["json"] == {"hello": "world", "update_day": "2026-04-16"}
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_run_http_get_parses_json_under_text_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内网 API 常见：body 是 JSON 但 Content-Type 标成 text/plain。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            content=json.dumps({"platform": "tmall", "ok": True}).encode(),
        )

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    token = http_tools.set_active_allowed_hosts(frozenset({"172.17.208.45:5004"}))
    try:
        out = await http_tools.run_http_get_json({
            "url": "http://172.17.208.45:5004/api/platform-updates/all",
        })
    finally:
        http_tools.reset_active_allowed_hosts(token)

    assert out["ok"] is True
    assert out["json"] == {"platform": "tmall", "ok": True}


@pytest.mark.asyncio
async def test_run_http_get_rejects_outside_whitelist() -> None:
    token = http_tools.set_active_allowed_hosts(frozenset({"api.example.com:443"}))
    try:
        out = await http_tools.run_http_get_json(
            {"url": "https://evil.example.com/x"},
        )
    finally:
        http_tools.reset_active_allowed_hosts(token)
    assert out["ok"] is False
    assert "白名单" in out["error"] or "允许" in out["error"]


@pytest.mark.asyncio
async def test_run_http_get_truncates_large_response(monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"X" * (http_tools.MAX_HTTP_RESPONSE_BYTES + 4096)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=big,
        )

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    token = http_tools.set_active_allowed_hosts(frozenset({"big.example.com:443"}))
    try:
        out = await http_tools.run_http_get_json({"url": "https://big.example.com/x"})
    finally:
        http_tools.reset_active_allowed_hosts(token)

    assert out["ok"] is True
    assert out["truncated"] is True
    # 截断后的 text 长度不应超过 limit
    assert len(out["text"]) <= http_tools.MAX_HTTP_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_run_http_get_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    token = http_tools.set_active_allowed_hosts(frozenset({"slow.example.com:443"}))
    try:
        out = await http_tools.run_http_get_json({"url": "https://slow.example.com/x"})
    finally:
        http_tools.reset_active_allowed_hosts(token)
    assert out["ok"] is False
    assert "timed out" in out["error"]


@pytest.mark.asyncio
async def test_run_http_post_sends_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        captured["method"] = request.method
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"received":true}',
        )

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    token = http_tools.set_active_allowed_hosts(frozenset({"api.example.com:443"}))
    try:
        out = await http_tools.run_http_post_json(
            {
                "url": "https://api.example.com/v1/echo",
                "json": {"foo": "bar"},
            },
        )
    finally:
        http_tools.reset_active_allowed_hosts(token)

    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {"foo": "bar"}
    assert out["ok"] is True
    assert out["json"] == {"received": True}


def test_normalize_headers_drops_unknown() -> None:
    out = http_tools._normalize_headers(  # noqa: SLF001
        {
            "User-Agent": "test/1",
            "Authorization": "Bearer xyz",
            "X-Forwarded-For": "1.2.3.4",  # 未在白名单
            "Cookie": "evil=1",  # 未在白名单
            12345: "ok",  # 非字符串 key
        },
    )
    assert out == {"User-Agent": "test/1", "Authorization": "Bearer xyz"}


# ── safe_run_tool 派发 http_* 通路 ──


class _FakeSession:
    async def execute(self, *_a, **_kw):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_safe_run_tool_rejects_http_when_no_skill_active() -> None:
    raw = await safe_invoke.safe_run_tool(
        _FakeSession(),
        http_tools.HTTP_GET_TOOL_NAME,
        '{"url":"https://x.example.com/y"}',
        active_system_skill_slugs=set(),
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=uuid.uuid4(),
        allowed_http_hosts=frozenset(),
    )
    out = json.loads(raw)
    assert out["ok"] is False
    assert "http_* tools" in out["error"]


@pytest.mark.asyncio
async def test_safe_run_tool_dispatches_http_with_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {"hosts": None}

    async def fake_run_http_tool(name: str, args_json: str) -> str:
        # 验证：safe_invoke 在调用前已经把白名单 set 进 ContextVar
        captured["hosts"] = http_tools.get_active_allowed_hosts()
        captured["name"] = name
        return json.dumps({"ok": True, "echo": json.loads(args_json)}, ensure_ascii=False)

    monkeypatch.setattr(safe_invoke, "run_http_tool", fake_run_http_tool)

    raw = await safe_invoke.safe_run_tool(
        _FakeSession(),
        http_tools.HTTP_GET_TOOL_NAME,
        '{"url":"http://172.17.208.45:5004/api/x"}',
        active_system_skill_slugs=set(),
        skill_id_by_tool_name={},
        allowed_platform_tools=frozenset(),
        session_id=None,
        project_id=uuid.uuid4(),
        allowed_http_hosts=frozenset({"172.17.208.45:5004"}),
    )
    out = json.loads(raw)
    assert out["ok"] is True
    assert captured["hosts"] == frozenset({"172.17.208.45:5004"})
    # 调用结束后 ContextVar 应被复位（外层默认 frozenset()）
    assert http_tools.get_active_allowed_hosts() == frozenset()


@pytest.mark.asyncio
async def test_safe_run_tool_resets_hosts_even_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(name: str, args_json: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(safe_invoke, "run_http_tool", boom)

    with pytest.raises(RuntimeError):
        await safe_invoke.safe_run_tool(
            _FakeSession(),
            http_tools.HTTP_GET_TOOL_NAME,
            '{"url":"http://x.example.com/y"}',
            active_system_skill_slugs=set(),
            skill_id_by_tool_name={},
            allowed_platform_tools=frozenset(),
            session_id=None,
            project_id=None,
            allowed_http_hosts=frozenset({"x.example.com:80"}),
        )
    assert http_tools.get_active_allowed_hosts() == frozenset()


# ── ContextVar 跨任务隔离（FastAPI 多请求并发场景） ──


@pytest.mark.asyncio
async def test_context_var_isolation_between_tasks() -> None:
    async def task(hosts: frozenset[str], expected: frozenset[str]) -> bool:
        token = http_tools.set_active_allowed_hosts(hosts)
        try:
            await asyncio.sleep(0)  # 主动让出
            return http_tools.get_active_allowed_hosts() == expected
        finally:
            http_tools.reset_active_allowed_hosts(token)

    a = asyncio.create_task(task(frozenset({"a:80"}), frozenset({"a:80"})))
    b = asyncio.create_task(task(frozenset({"b:80"}), frozenset({"b:80"})))
    res_a, res_b = await asyncio.gather(a, b)
    assert res_a is True
    assert res_b is True


# ── schema 暴露契约 ──


def test_skill_http_outbound_proxy_prefers_skill_then_ui_then_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SKILL_HTTP_PROXY", "http://skill-proxy:1", raising=False)
    monkeypatch.setattr(settings, "UI_HTTP_LOGIN_PROXY", "http://ui-proxy:1", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:1")
    assert http_tools.skill_http_outbound_proxy() == "http://skill-proxy:1"

    monkeypatch.setattr(settings, "SKILL_HTTP_PROXY", None, raising=False)
    assert http_tools.skill_http_outbound_proxy() == "http://ui-proxy:1"

    monkeypatch.setattr(settings, "SKILL_HTTP_PROXY", None, raising=False)
    monkeypatch.setattr(settings, "UI_HTTP_LOGIN_PROXY", None, raising=False)
    assert http_tools.skill_http_outbound_proxy() == "http://env-proxy:1"


def test_skill_http_outbound_proxy_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SKILL_HTTP_PROXY", None, raising=False)
    monkeypatch.setattr(settings, "UI_HTTP_LOGIN_PROXY", None, raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    assert http_tools.skill_http_outbound_proxy() is None


def test_http_tool_schemas_have_required_shape() -> None:
    schemas = http_tools.http_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {http_tools.HTTP_GET_TOOL_NAME, http_tools.HTTP_POST_TOOL_NAME}
    for s in schemas:
        params = s["function"]["parameters"]
        assert params["type"] == "object"
        assert "url" in params["properties"]
        assert "url" in params["required"]


def test_is_http_tool_predicate() -> None:
    assert http_tools.is_http_tool("http_get_json")
    assert http_tools.is_http_tool("http_post_json")
    assert not http_tools.is_http_tool("web_search")
    assert not http_tools.is_http_tool("platform_search_testcases")
