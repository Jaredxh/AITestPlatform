"""统一 LLM 供应商封装，基于 OpenAI SDK 兼容协议。

所有支持 OpenAI 兼容 API 的供应商（OpenAI / DeepSeek / 通义千问 / Ollama / 自定义）
都通过 AsyncOpenAI 客户端调用，只需配置不同的 base_url 和 api_key。
"""

import time
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ollama": "http://localhost:11434/v1",
}


def build_client(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncOpenAI:
    """构造 OpenAI-compat 客户端。

    api_key 留空时只对 Ollama / 自部署 provider 兜底成 ``"ollama"`` 占位
    （Ollama 默认不校验 key）；其他 provider 留空让 OpenAI SDK 自己抛
    AuthenticationError 而不是用假 key 打真接口产生迷惑性 401。
    """
    resolved_base = base_url or PROVIDER_BASE_URLS.get(provider)
    if api_key:
        resolved_key = api_key
    elif provider == "ollama":
        resolved_key = "ollama"
    else:
        # 让上层立刻看到"没配 api_key"，而不是发起一次注定 401 的请求
        raise ValueError(
            f"provider={provider!r} 未提供 api_key；"
            "请到「系统设置 → LLM 配置」补全 API Key 后重试"
        )
    return AsyncOpenAI(api_key=resolved_key, base_url=resolved_base)


async def test_connection(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict:
    """发送一个轻量请求测试 LLM 连通性，返回结果摘要。"""
    client = build_client(provider, api_key, base_url)
    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi, reply with exactly: ok"}],
            max_tokens=10,
            temperature=0,
        )
        elapsed = int((time.monotonic() - start) * 1000)
        content = response.choices[0].message.content or ""
        return {
            "success": True,
            "message": f"连接成功，模型响应: {content.strip()[:50]}",
            "model": response.model,
            "response_time_ms": elapsed,
        }
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "message": f"连接失败: {type(e).__name__}: {e}",
            "model": None,
            "response_time_ms": elapsed,
        }
    finally:
        await client.close()


async def stream_chat(
    provider: str,
    model: str,
    messages: list[dict],
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> AsyncGenerator[ChatCompletionChunk, None]:
    """流式对话，yield 每个 chunk。调用方负责 close client。

    - 容错：清理模型名前后多余空白；
    - usage 信息通过 ``stream_options.include_usage`` 在末尾 chunk 返回，
      便于上层记录 token 用量；
    - 不在请求里硬塞 ``stream_options``，部分自定义 OpenAI-compat 网关不识别，
      失败后无 stream_options 重试一次。
    """
    client = build_client(provider, api_key, base_url)
    cleaned_model = (model or "").strip()
    base_kwargs: dict = {
        "model": cleaned_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        base_kwargs["tools"] = tools
        # 'auto' is the OpenAI default — make it explicit so some gateways
        # (e.g. GLM) that require it present don't silently ignore tools.
        base_kwargs["tool_choice"] = tool_choice or "auto"
    try:
        try:
            stream = await client.chat.completions.create(
                **base_kwargs,
                stream_options={"include_usage": True},
            )
        except Exception:  # noqa: BLE001
            stream = await client.chat.completions.create(**base_kwargs)
        async for chunk in stream:
            yield chunk
    finally:
        await client.close()


async def complete_chat(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    """Non-streaming chat completion for small internal planning tasks.

    Used by the web-search agent to let the configured model rewrite the
    user's question into precise search queries before retrieval happens.
    """
    client = build_client(provider, api_key, base_url)
    try:
        response = await client.chat.completions.create(
            model=(model or "").strip(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return response.choices[0].message.content or ""
    finally:
        await client.close()
