"""三期 chat 通路全局 ``platform_*`` 工具（OpenAI schema + TOOL_REGISTRY 执行桥接）。

Task 12.4：从 ``platform_chat_tools`` 迁到此模块；``platform_chat_tools`` 保留兼容 re-export。
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.llm.agent_tools import register_tool
from app.modules.testcases.models import Testcase
from app.modules.ui_automation.execution_service import start_execution
from app.modules.ui_automation.models import TestEnvironment
from app.modules.ui_automation.schemas import ExecutionCreateRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatPlatformRuntime:
    db: AsyncSession
    user: User
    project_id: uuid.UUID
    llm_config_id: uuid.UUID | None
    assistant_message_id: uuid.UUID | None
    #: Phase 13 / Task 13.2 — 当前 chat session id（chat_context 注入路径）。
    #: 仅在 chat 入口挂载；老路径（直接调 platform tool）一律 None，对老逻辑透明。
    session_id: uuid.UUID | None = None
    #: Phase 13 / Task 13.2 — 当前会话上下文（``ChatSession.chat_context`` 浅拷贝）。
    #: env_priority Layer 2 据此读"上次 confirm 过的 environment_id"。
    chat_context: dict | None = None
    #: Phase 13 / Task 13.2 — 本轮用户原始消息（不含 system / assistant）。
    #: env_priority Layer 1 据此抽"用 staging 跑"等显式提及。
    user_message: str | None = None


_rt_var: ContextVar[ChatPlatformRuntime | None] = ContextVar(
    "chat_platform_runtime",
    default=None,
)


def _get_runtime() -> ChatPlatformRuntime | None:
    return _rt_var.get()


@asynccontextmanager
async def chat_platform_runtime_cm(
    db: AsyncSession,
    user: User,
    project_id: uuid.UUID | None,
    llm_config_id: uuid.UUID | None,
    assistant_message_id: uuid.UUID | None,
    *,
    session_id: uuid.UUID | None = None,
    chat_context: dict | None = None,
    user_message: str | None = None,
):
    """在单次 chat agent 循环内挂载 platform 工具运行时。

    Phase 13 / Task 13.2 增量：``session_id`` / ``chat_context`` /
    ``user_message`` 全部可选，老调用方（test fixtures、其它 platform tool 直
    调路径）不传也能跑——env_priority 的 Layer 1 / Layer 2 会跳过解析回退到下
    一层，行为与 Phase 12 等价。
    """
    if project_id is None:
        yield
        return

    rt = ChatPlatformRuntime(
        db=db,
        user=user,
        project_id=project_id,
        llm_config_id=llm_config_id,
        assistant_message_id=assistant_message_id,
        session_id=session_id,
        chat_context=chat_context,
        user_message=user_message,
    )
    token = _rt_var.set(rt)
    try:
        yield
    finally:
        _rt_var.reset(token)


def platform_chat_openai_schemas() -> dict[str, dict[str, Any]]:
    """``platform_*`` → OpenAI Chat tool spec（function.name 无前缀）。"""
    return {
        "platform_search_testcases": {
            "type": "function",
            "function": {
                "name": "platform_search_testcases",
                "description": (
                    "在当前项目下按标题关键字搜索 UI 测试用例，返回 id 与标题。"
                    "用户没说具体用例名时可不传 query，返回最近更新的若干条。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "标题模糊匹配关键字；可省略",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "最多返回条数，默认 10，最大 30",
                            "default": 10,
                        },
                    },
                },
            },
        },
        "platform_list_environments": {
            "type": "function",
            "function": {
                "name": "platform_list_environments",
                "description": (
                    "列出当前项目下可用的 UI 自动化测试环境（Playwright 目标）。"
                    "执行用例前用于确认 env_id。"
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "platform_run_ui_execution": {
            "type": "function",
            "function": {
                "name": "platform_run_ui_execution",
                "description": (
                    "启动一批 UI 自动化用例执行（二期 ExecutionEngine）。"
                    "返回 execution_id 供前端 SSE / 列表查看进度。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "testcase_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "用例 UUID 字符串列表",
                        },
                        "environment_id": {
                            "type": "string",
                            "description": "环境 UUID；可省略则服务端取项目最新环境",
                        },
                    },
                    "required": ["testcase_ids"],
                },
            },
        },
    }


_PLATFORM_REGISTERED = False


def ensure_platform_tools_registered() -> None:
    """进程级一次性注册 chat platform 工具到 ``TOOL_REGISTRY``。"""
    global _PLATFORM_REGISTERED
    if _PLATFORM_REGISTERED:
        return
    register_tool("platform_search_testcases", _exec_search_testcases)
    register_tool("platform_list_environments", _exec_list_environments)
    register_tool("platform_run_ui_execution", _exec_run_ui_execution)
    _PLATFORM_REGISTERED = True


async def _exec_search_testcases(args: dict[str, Any]) -> dict[str, Any]:
    rt = _get_runtime()
    if rt is None:
        return {"error": "platform_search_testcases requires an active chat runtime"}

    limit = int(args.get("limit") or 10)
    limit = max(1, min(limit, 30))
    q = (args.get("query") or "").strip()

    if q:
        like = f"%{q}%"
        stmt = (
            select(Testcase)
            .where(Testcase.project_id == rt.project_id, Testcase.title.ilike(like))
            .order_by(Testcase.updated_at.desc())
            .limit(limit)
        )
    else:
        stmt = (
            select(Testcase)
            .where(Testcase.project_id == rt.project_id)
            .order_by(Testcase.updated_at.desc())
            .limit(limit)
        )

    result = await rt.db.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "testcases": [
            {"id": str(t.id), "title": t.title, "case_no": t.case_no}
            for t in rows
        ],
    }


async def _exec_list_environments(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    rt = _get_runtime()
    if rt is None:
        return {"error": "platform_list_environments requires an active chat runtime"}

    stmt = (
        select(TestEnvironment)
        .where(TestEnvironment.project_id == rt.project_id)
        .order_by(TestEnvironment.updated_at.desc())
        .limit(50)
    )
    result = await rt.db.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "count": len(rows),
        "environments": [
            {
                "id": str(e.id),
                "name": e.name,
                "base_url": str(e.base_url),
            }
            for e in rows
        ],
    }


async def _exec_run_ui_execution(args: dict[str, Any]) -> dict[str, Any]:
    rt = _get_runtime()
    if rt is None:
        return {"error": "platform_run_ui_execution requires an active chat runtime"}

    raw_ids = args.get("testcase_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return {"error": "testcase_ids required"}

    tc_ids: list[uuid.UUID] = []
    for x in raw_ids:
        try:
            tc_ids.append(uuid.UUID(str(x)))
        except (ValueError, TypeError):
            return {"error": f"invalid testcase_id: {x!r}"}

    env_raw = args.get("environment_id")
    env_id: uuid.UUID | None = None
    if env_raw not in (None, ""):
        try:
            env_id = uuid.UUID(str(env_raw))
        except (ValueError, TypeError):
            return {"error": f"invalid environment_id: {env_raw!r}"}

    req = ExecutionCreateRequest(
        testcase_ids=tc_ids,
        environment_id=env_id,
        llm_config_id=rt.llm_config_id,
        chat_message_id=rt.assistant_message_id,
    )
    try:
        item = await start_execution(rt.db, rt.project_id, req, rt.user)
    except Exception as exc:  # noqa: BLE001
        logger.exception("platform_run_ui_execution failed")
        return {"error": str(exc)}

    return {
        "execution_id": str(item.id),
        "status": item.status,
        "message": "已创建执行任务，可在 UI 自动化执行列表或 SSE 查看进度",
    }
