"""Skill 通用 HTTP 工具桥接（Phase 12 / Task 12.x 优化）。

为「OpenClaw / Claude Code 风格」技能包补一组常用的 HTTP GET/POST 工具，让用户
导入的 SKILL.md 里写的 `GET http://...` 能真正跑起来——之前缺这一块，所以
``cq-qa-financial-reportcheck`` 这种"看文档调接口"的 skill 始终拿不到数据。

设计要点（产品 / 安全双视角）：

1. **文档自我授权**：URL 的 host:port 必须在该 skill 的 SKILL.md 正文里明文出现
   过。LLM 不能自由发起任意外部请求——它只能调 SKILL.md 已经"写明"的接口，
   彻底封死 SSRF + LLM 跨权限指挥。
2. **零 yaml 改造**：用户不必在 SKILL.md frontmatter 加 ``tools_required:
   [http_get_json]``——只要正文里写了 ``http(s)://...``，``compose`` 阶段就会
   自动把 host 加入白名单并给 LLM 暴露 http_* 工具。
3. **资源闸**：
   - 仅 ``http://`` / ``https://``；
   - 响应体 ≤ 256 KB（再大就截断 + 提示）；
   - 超时：连接 ≤8 s、读 ≤22 s（避免长时间无响应「卡住」感）；
   - **VPN**：默认 ``trust_env=False``，但会读取 ``SKILL_HTTP_PROXY`` /
     ``UI_HTTP_LOGIN_PROXY`` / ``HTTP_PROXY`` 作为 **唯一** 显式代理出口
     （与 ``docker-compose.vpn.yml`` 一致），避免 macOS Docker 直连私网超时；
   - 阻断回环（``localhost`` / ``127.0.0.0/8``）除非明文写在 SKILL.md。
"""

from __future__ import annotations

import contextvars
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.config import settings

HTTP_GET_TOOL_NAME = "http_get_json"
HTTP_POST_TOOL_NAME = "http_post_json"

MAX_HTTP_RESPONSE_BYTES = 256 * 1024
HTTP_CONNECT_TIMEOUT = 8.0
HTTP_READ_TIMEOUT = 22.0
HTTP_CLIENT_TIMEOUT = httpx.Timeout(HTTP_READ_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
# 测试 / 外部引用「读超时秒数」时的别名
HTTP_TIMEOUT_SECONDS = HTTP_READ_TIMEOUT
ALLOWED_HEADER_NAMES = frozenset(
    [
        "accept",
        "accept-language",
        "authorization",
        "content-type",
        "user-agent",
        "x-requested-with",
    ],
)


# ── ContextVar：当前 turn 内可访问的 host 白名单 ──────────────────
#
# ContextVar 而非全局变量：FastAPI 的每个请求是独立 task，互相不干扰；
# ``chat_service`` 在每条消息处理开始时 set，在结束时 reset，避免泄漏到下一条。

_active_allowed_hosts: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "skill_http_allowed_hosts",
    default=frozenset(),
)


def set_active_allowed_hosts(hosts: frozenset[str]) -> contextvars.Token:
    return _active_allowed_hosts.set(hosts)


def reset_active_allowed_hosts(token: contextvars.Token) -> None:
    _active_allowed_hosts.reset(token)


def get_active_allowed_hosts() -> frozenset[str]:
    return _active_allowed_hosts.get()


# ── host 提取：从 SKILL.md 正文中找出所有 http(s)://host[:port] ──

# 注意：URL 提取要兼容文档里常见的两种形式：
#   1. ``GET http://172.17.208.45:5004/api/...``（裸 URL）
#   2. ``[link](http://example.com/x)``（Markdown 链接）
# 都用同一条正则——以 http/https:// 开头，截到第一个空白/反引号/中括号即可。
_URL_RE = re.compile(r"https?://[^\s`'\"<>)\]]+", re.IGNORECASE)


def _norm_host(scheme: str, host: str, port: int | None) -> str:
    """把 ``(scheme, host, port)`` 归一为 ``host:port`` 字符串供白名单比对。

    当 URL 没显式写端口时，按 scheme 默认（http=80 / https=443）填补，避免
    "SKILL.md 写 ``http://api.foo.com:80/x``，LLM 调 ``http://api.foo.com/x``"
    被误判为不在白名单。
    """
    h = host.lower().strip()
    if port is None:
        port = 443 if scheme.lower() == "https" else 80
    return f"{h}:{port}"


def extract_allowed_hosts_from_body(body: str) -> set[str]:
    """从 SKILL.md 正文抽取所有 http(s) URL 的 ``host:port``。

    返回 set 形式，调用方可以再 ``frozenset(...)`` 锁定。
    """
    if not body:
        return set()
    out: set[str] = set()
    for raw in _URL_RE.findall(body):
        try:
            parsed = urlsplit(raw)
        except ValueError:
            continue
        if parsed.scheme.lower() not in ("http", "https"):
            continue
        if not parsed.hostname:
            continue
        out.add(_norm_host(parsed.scheme, parsed.hostname, parsed.port))
    return out


# ── 安全检查：URL 必须命中白名单，且不可访问回环 ──────────────────


def _is_loopback_host(host: str) -> bool:
    """``localhost`` 或 ``127.0.0.0/8`` 视为回环。"""
    h = host.lower().strip()
    if h in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass
class HttpCheckResult:
    ok: bool
    error: str | None = None
    normalized_host: str | None = None


def check_url_against_allowed_hosts(
    url: str,
    allowed_hosts: frozenset[str],
) -> HttpCheckResult:
    """对入参 URL 做完整的安全 + 白名单校验。"""
    if not url or not isinstance(url, str):
        return HttpCheckResult(False, "url 必须是非空字符串")
    try:
        parsed = urlsplit(url.strip())
    except ValueError as e:
        return HttpCheckResult(False, f"url 解析失败: {e}")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return HttpCheckResult(
            False,
            "仅允许 http:// 或 https:// 协议，禁止 file/ftp/data/etc.",
        )
    host = parsed.hostname or ""
    if not host:
        return HttpCheckResult(False, "url 缺少 host")
    norm = _norm_host(scheme, host, parsed.port)
    if _is_loopback_host(host) and norm not in allowed_hosts:
        return HttpCheckResult(
            False,
            f"禁止访问回环地址 {host}，除非该 host 在 SKILL.md 中明文出现",
        )
    if norm not in allowed_hosts:
        # 给一个友善的解释——LLM 看到这条会知道下次只能调文档里的接口
        return HttpCheckResult(
            False,
            (
                f"host {norm!r} 不在当前激活技能的允许列表里。"
                "白名单基于 SKILL.md 正文中明文出现的 http(s) 地址自动构建；"
                f"当前允许：{sorted(allowed_hosts) if allowed_hosts else '空（该技能未声明任何 URL）'}。"
            ),
        )
    return HttpCheckResult(True, None, norm)


# ── 工具实现：http_get_json / http_post_json ─────────────────────


def _normalize_headers(raw: Any) -> dict[str, str]:
    """白名单过滤 headers——避免 LLM 注入怪异头（如 ``X-Forwarded-For`` 伪造）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, (str, int, float)):
            continue
        if k.lower() not in ALLOWED_HEADER_NAMES:
            continue
        out[k] = str(v)
    return out


def skill_http_outbound_proxy() -> str | None:
    """技能包 HTTP 出口代理 URL，与 ``docker-compose.vpn`` 场景对齐。

    macOS Docker 内 LinuxKit VM **不继承宿主机 VPN 路由**，直连 ``172.x`` 会超时；
    经 ``host.docker.internal:8118`` 等本机 HTTP 代理出去即可命中 VPN。

    优先级：``SKILL_HTTP_PROXY`` → ``UI_HTTP_LOGIN_PROXY`` → ``HTTP_PROXY`` / 小写同名 /
    ``ALL_PROXY``。均未设置则 ``None``（直连）。

    仍配合 URL host 白名单（SKILL.md 自我授权），不因开代理而扩大 SSRF 面。
    """
    for raw in (
        (settings.SKILL_HTTP_PROXY or "").strip(),
        (settings.UI_HTTP_LOGIN_PROXY or "").strip(),
        (os.environ.get("HTTP_PROXY") or "").strip(),
        (os.environ.get("http_proxy") or "").strip(),
        (os.environ.get("ALL_PROXY") or "").strip(),
        (os.environ.get("all_proxy") or "").strip(),
    ):
        if raw:
            return raw
    return None


def _truncate_body(body: bytes) -> tuple[str, bool]:
    """把响应 body decode 成 utf-8 字符串，并按 ``MAX_HTTP_RESPONSE_BYTES`` 截断。"""
    truncated = False
    if len(body) > MAX_HTTP_RESPONSE_BYTES:
        body = body[:MAX_HTTP_RESPONSE_BYTES]
        truncated = True
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = body.decode("latin-1", errors="replace")
    return text, truncated


async def _do_request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None,
    json_body: Any,
    headers: dict[str, str],
) -> dict[str, Any]:
    try:
        proxy = skill_http_outbound_proxy()
        client_kw: dict[str, Any] = {
            "timeout": HTTP_CLIENT_TIMEOUT,
            "follow_redirects": True,
            # 不显式 trust_env：代理 URL 只来自上面白名单配置，避免未预期的系统代理
            "trust_env": False,
        }
        if proxy:
            client_kw["proxy"] = proxy

        async with httpx.AsyncClient(**client_kw) as client:
            req = client.build_request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers or None,
            )
            resp = await client.send(req)
            raw = resp.content
            text, truncated = _truncate_body(raw)
            content_type = (resp.headers.get("content-type") or "").lower()
            payload: dict[str, Any] = {
                "ok": resp.is_success,
                "status_code": resp.status_code,
                "content_type": content_type,
                "url": str(resp.url),
                "truncated": truncated,
                "size_bytes": len(raw),
            }
            if proxy:
                payload["via_proxy"] = True
            if "application/json" in content_type or "+json" in content_type:
                try:
                    payload["json"] = json.loads(text)
                except json.JSONDecodeError:
                    payload["text"] = text
            else:
                # 内网 API 常把 JSON 标成 text/plain；启发式再试解析
                stripped = text.lstrip("\ufeff").lstrip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        payload["json"] = json.loads(stripped)
                    except json.JSONDecodeError:
                        payload["text"] = text
                else:
                    payload["text"] = text
            return payload
    except httpx.TimeoutException:
        return {
            "ok": False,
            "error": (
                f"http request timed out "
                f"(connect ≤{HTTP_CONNECT_TIMEOUT}s, read ≤{HTTP_READ_TIMEOUT}s)"
            ),
        }
    except httpx.RequestError as e:
        return {"ok": False, "error": f"http request failed: {e!s}"}


async def run_http_get_json(args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    params = args.get("params")
    if not isinstance(url, str):
        return {"ok": False, "error": "missing url"}
    if params is not None and not isinstance(params, dict):
        return {"ok": False, "error": "params must be an object/dict if provided"}
    headers = _normalize_headers(args.get("headers"))
    check = check_url_against_allowed_hosts(url, get_active_allowed_hosts())
    if not check.ok:
        return {"ok": False, "error": check.error}
    # urlencode 验证 params 都能 stringify
    if params:
        try:
            urlencode(params, doseq=True)
        except (TypeError, UnicodeEncodeError) as e:
            return {"ok": False, "error": f"params not encodable: {e!s}"}
    return await _do_request(
        "GET",
        url,
        params=params or None,
        json_body=None,
        headers=headers,
    )


async def run_http_post_json(args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    body = args.get("json") or args.get("body")
    if not isinstance(url, str):
        return {"ok": False, "error": "missing url"}
    headers = _normalize_headers(args.get("headers"))
    check = check_url_against_allowed_hosts(url, get_active_allowed_hosts())
    if not check.ok:
        return {"ok": False, "error": check.error}
    return await _do_request(
        "POST",
        url,
        params=None,
        json_body=body,
        headers=headers,
    )


# ── OpenAI tool schemas ──────────────────────────────────────────


def http_get_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": HTTP_GET_TOOL_NAME,
            "description": (
                "对当前激活技能（SKILL.md）中明文写过的 http(s) 接口发起 GET 请求并返回 JSON / 文本。\n"
                "**必须使用此工具**调用 SKILL.md 中描述的 GET 接口；不要"
                "凭脑补返回数据。返回字段：ok / status_code / json / text / "
                "content_type / size_bytes / truncated。\n"
                "安全约束：URL 的 host:port 必须在当前技能 SKILL.md 中明文出现；"
                "响应 256 KB 截断；读超时约 22s。"
                "Docker+VPN 部署若直连超时，请在环境配置 SKILL_HTTP_PROXY 或 "
                "UI_HTTP_LOGIN_PROXY（与本机 mitmproxy 等一致）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "完整的 http/https URL",
                    },
                    "params": {
                        "type": "object",
                        "description": "URL query 参数键值对（可选）",
                    },
                    "headers": {
                        "type": "object",
                        "description": (
                            "可选请求头；仅放行 accept / authorization / content-type "
                            "/ user-agent / x-requested-with / accept-language"
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    }


def http_post_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": HTTP_POST_TOOL_NAME,
            "description": (
                "对当前激活技能（SKILL.md）中明文写过的 http(s) 接口发起 POST JSON 请求并返回 JSON / 文本。\n"
                "**必须使用此工具**调用 SKILL.md 中描述的 POST 接口。\n"
                "安全约束同 http_get_json。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的 http/https URL"},
                    "json": {
                        "description": "请求体（任意 JSON 序列化对象/数组/标量）",
                    },
                    "headers": {
                        "type": "object",
                        "description": "可选请求头；白名单同 http_get_json",
                    },
                },
                "required": ["url"],
            },
        },
    }


def http_tool_schemas() -> list[dict[str, Any]]:
    return [http_get_tool_schema(), http_post_tool_schema()]


# ── 同步入口（供 safe_invoke 派发）──

_TOOL_RUNNERS = {
    HTTP_GET_TOOL_NAME: run_http_get_json,
    HTTP_POST_TOOL_NAME: run_http_post_json,
}


def is_http_tool(name: str) -> bool:
    return name in _TOOL_RUNNERS


async def run_http_tool(name: str, args_json: str) -> str:
    """``safe_invoke`` 派发入口；返回 JSON 字符串供 OpenAI tool role 回灌。"""
    runner = _TOOL_RUNNERS.get(name)
    if runner is None:
        return json.dumps({"ok": False, "error": f"unknown http tool: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(args_json) if args_json else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    payload = await runner(args)
    return json.dumps(payload, ensure_ascii=False)


# ── 单元测试便捷 hook ──
__all__ = [
    "HTTP_GET_TOOL_NAME",
    "HTTP_POST_TOOL_NAME",
    "MAX_HTTP_RESPONSE_BYTES",
    "HTTP_TIMEOUT_SECONDS",
    "check_url_against_allowed_hosts",
    "extract_allowed_hosts_from_body",
    "get_active_allowed_hosts",
    "http_get_tool_schema",
    "http_post_tool_schema",
    "http_tool_schemas",
    "is_http_tool",
    "reset_active_allowed_hosts",
    "run_http_get_json",
    "run_http_post_json",
    "run_http_tool",
    "set_active_allowed_hosts",
    "skill_http_outbound_proxy",
]
