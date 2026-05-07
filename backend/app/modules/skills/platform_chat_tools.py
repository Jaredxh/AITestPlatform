"""Backward-compatible re-export（Task 12.4 ``platform_tools``）。"""

from __future__ import annotations

from app.modules.skills.platform_tools import (
    ChatPlatformRuntime,
    chat_platform_runtime_cm,
    ensure_platform_tools_registered,
    platform_chat_openai_schemas,
)

ensure_platform_chat_tools_registered = ensure_platform_tools_registered

__all__ = [
    "ChatPlatformRuntime",
    "chat_platform_runtime_cm",
    "ensure_platform_chat_tools_registered",
    "ensure_platform_tools_registered",
    "platform_chat_openai_schemas",
]
