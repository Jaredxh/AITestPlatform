"""``system__ui_automation__propose_execution_plan`` 工具（Phase 13 / Task 13.1）。

LLM 选好用例 + 环境后**必须**走这一步生成 ConfirmationCard payload；后端
缓存 ``plan_id``（TTL 10 分钟），用户在前端点"确认执行"后由
``POST /api/ui-executions { plan_id, source: 'chat', triggered_chat_session_id }``
（task 13.3 接通）反查 plan 真正派发——LLM 永远拿不到也不能直接调
``run_ui_test``，这是设计文档 §10.3.3 的最后一道安全闸门。

Phase 13 / Task 13.3：tool 调用成功后，在独立 DB session 里把 ConfirmationCard
落成一条 ``kind='skill_card'`` 的 ``ChatMessage``（关联当前 session_id），
并通过 ``SYSTEM_EVENT_BUS`` 广播给前端。chat 流主循环使用的 ``rt.db`` 仍在
LLM 长流中，**不能**在那个 session 上 commit；必须自起 session 写库。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.database import async_session_factory
from app.modules.skills.builtin.ui_automation.plan_builder import (
    build_execution_plan,
    update_cached_plan_skill_card,
)
from app.modules.skills.platform_tools import _get_runtime

logger = logging.getLogger(__name__)


PROPOSE_EXECUTION_PLAN_TOOL_NAME = "system__ui_automation__propose_execution_plan"

PROPOSE_EXECUTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": PROPOSE_EXECUTION_PLAN_TOOL_NAME,
        "description": (
            "生成 UI 自动化执行的 ConfirmationCard 协议（含 plan_id + cases + "
            "environment + risk_level + 物料预览 + confirmation_strength）。"
            "返回后由前端渲染确认卡片，用户点'确认执行'后由前端走专门 API "
            "派发。AI 不能直接触发执行——本 tool 只是生成 plan 草案，真正落地 "
            "由用户决策。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "case_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "本次计划要执行的用例 UUID 列表（顺序即执行顺序，"
                        "支持多用例编排）；至少 1 条。"
                    ),
                },
                "environment_id": {
                    "type": "string",
                    "description": "目标环境 UUID（必填，不允许 AI 默认）",
                },
                "llm_config_id": {
                    "type": "string",
                    "description": (
                        "本次执行使用的 LLM 配置 UUID；省略时用项目默认配置。"
                    ),
                },
            },
            "required": ["case_ids", "environment_id"],
        },
    },
}


async def exec_propose_execution_plan(args: dict[str, Any]) -> dict[str, Any]:
    rt = _get_runtime()
    if rt is None:
        return {
            "error": (
                "propose_execution_plan requires an active chat runtime "
                "(no project_id bound)"
            ),
        }

    raw_case_ids = args.get("case_ids") or []
    if not isinstance(raw_case_ids, list) or not raw_case_ids:
        return {"error": "case_ids must be a non-empty array of UUID strings"}

    case_ids: list[uuid.UUID] = []
    for raw in raw_case_ids:
        try:
            case_ids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            return {"error": f"invalid case_id: {raw!r}"}

    env_raw = args.get("environment_id")
    if not env_raw:
        return {"error": "environment_id is required (do not let AI default it)"}
    try:
        env_id = uuid.UUID(str(env_raw))
    except (TypeError, ValueError):
        return {"error": f"invalid environment_id: {env_raw!r}"}

    llm_raw = args.get("llm_config_id")
    llm_id: uuid.UUID | None = None
    if llm_raw:
        try:
            llm_id = uuid.UUID(str(llm_raw))
        except (TypeError, ValueError):
            return {"error": f"invalid llm_config_id: {llm_raw!r}"}

    try:
        plan = await build_execution_plan(
            rt.db,
            project_id=rt.project_id,
            user=rt.user,
            case_ids=case_ids,
            environment_id=env_id,
            llm_config_id=llm_id or rt.llm_config_id,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("propose_execution_plan failed")
        return {"error": f"unexpected error: {exc}"}

    # Phase 13 / Task 13.3 — 把 plan 持久化为 kind=skill_card 消息 + 总线广播。
    # 失败兜底：plan 已生成且缓存（plan_id 仍可用），仅丢"实时推送 + 历史持久
    # 化"——LLM 仍可在下一句让用户主动刷新页面看到卡片，避免本调用整体失败。
    if rt.session_id is not None:
        try:
            from app.modules.llm.system_event_service import publish_skill_card

            payload = plan.model_dump(mode="json")
            async with async_session_factory() as bg_db:
                msg = await publish_skill_card(
                    bg_db,
                    session_id=rt.session_id,
                    plan_id=plan.plan_id,
                    plan_payload=payload,
                )
            if msg is not None:
                await update_cached_plan_skill_card(plan.plan_id, msg.id)
                # 刷新 plan 的 skill_card_message_id 字段一并返回给 LLM —— 后续
                # LLM 文本回复可以引用 message_id（虽然 LLM 不主动消费，但供前端
                # 端到端 trace 用）。
                payload["skill_card_message_id"] = str(msg.id)
                return payload
        except Exception:  # noqa: BLE001
            logger.exception("propose_execution_plan: persist skill_card failed; continuing")

    return plan.model_dump(mode="json")
