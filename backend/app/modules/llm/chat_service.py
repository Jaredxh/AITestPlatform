import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.database import async_session_factory
from app.modules.auth.models import User
from app.modules.llm.agent_tools import TOOLS, build_agent_system_guidance
from app.modules.llm.intent_handler import (
    DetectedIntent,
    IntentType,
    build_action_metadata,
    detect_intent,
    format_generation_result,
    format_review_result,
    resolve_document,
)
from app.modules.llm.models import ChatMessage, ChatSession, LLMConfig
from app.modules.llm.providers import stream_chat
from app.modules.llm.schemas import (
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    ChatSessionUpdateRequest,
)
from app.modules.skills.models import SkillUsageLog
from app.modules.skills.platform_chat_tools import chat_platform_runtime_cm
from app.modules.skills.safe_invoke import safe_run_tool
from app.modules.skills.skill_router import SkillContext, compose

logger = logging.getLogger(__name__)


# ─────────────── SSE event helpers ───────────────
#
# 统一以 JSON 编码 data: 字段，避免 markdown 中的 \n 把 SSE 事件切碎导致前端
# 渲染丢格式。所有事件结构: {"type": "<kind>", ...}
#   type=delta      增量正文（原样追加到当前消息）
#   type=reasoning  增量思考（reasoning_content，可选展示）
#   type=info       状态/提示信息（联网中、生成中等）
#   type=action     业务动作完成事件（携带 metadata 与最终内容）
#   type=error      错误信息
#   type=done       本轮结束


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _sse_delta(content: str) -> str:
    return _sse({"type": "delta", "content": content})


def _sse_reasoning(content: str) -> str:
    return _sse({"type": "reasoning", "content": content})


def _sse_info(message: str) -> str:
    return _sse({"type": "info", "message": message})


def _sse_done() -> str:
    return _sse({"type": "done"})


def _sse_error(message: str) -> str:
    return _sse({"type": "error", "message": message})


def _sse_action(content: str, meta: dict | None = None) -> str:
    return _sse({"type": "action", "content": content, "meta": meta or {}})


def _sse_usage(total_tokens: int) -> str:
    """LLM 流末尾累计 ``total_tokens`` 的内部事件。

    历史 bug：``skip_persistence=True`` 下 _handle_chat_stream 算出 usage_total
    后只走"自己写库"分支用过，编排器（``_run_chat_task``）根本拿不到
    tokens 数；assistant ChatMessage.tokens_used 与 SkillUsageLog.tokens_consumed
    全是 NULL；前端"使用统计"页 avg_tokens 永远是 ``—``。
    现在在流末尾显式 emit ``type=usage``，让 orchestrator 写到 state
    （并由 persist() 反查回填 SkillUsageLog.tokens_consumed）。前端不展示
    该事件，因此不会出现在用户视野里。
    """
    return _sse({"type": "usage", "total_tokens": int(total_tokens)})


def _sse_skill_activated(payload: dict) -> str:
    """Phase 12 / Task 12.6 — 通知前端 SkillActivationHint banner。"""
    return _sse({"type": "skill_activated", **payload})


# ─────────────── Chat stream hub (background-task architecture) ───────────────
#
# Chat 和 testcase generation 一样，改成"后台任务 + 进程内 pub-sub"模式：
#   1. POST /send 只做"创建占位消息 + 派发后台任务 + 立刻返回"；
#   2. 后台任务独立拥有自己的 DB session，流式拉 LLM 输出并广播到 hub、
#      同时定期把已生成的内容 flush 到数据库里的占位消息；
#   3. GET /messages/{id}/stream 负责 SSE 订阅：活任务从 hub 实时读，
#      已完成任务直接从数据库回放最终内容；
#   4. 刷新/切页不再导致生成中断——生成任务跑在后台独立 asyncio task 里，
#      客户端只是 subscribe 当前状态，随时重新 subscribe 都能拿到完整内容。

import time  # for stream eviction


class _ChatStream:
    """单条 assistant 消息的事件缓冲广播器。

    与 testcase 的 _BatchStream 结构一致：chunks 是 (event_name, data_dict)
    列表；done 结束标志；cond 做生产-消费同步。
    """

    __slots__ = ("chunks", "done", "_cond", "created_at")

    def __init__(self) -> None:
        self.chunks: list[tuple[str, dict]] = []
        self.done: bool = False
        self._cond: asyncio.Condition = asyncio.Condition()
        self.created_at: float = time.monotonic()

    async def append(self, event: str, data: dict) -> None:
        async with self._cond:
            self.chunks.append((event, data))
            if event in ("done", "error_terminal"):
                self.done = True
            self._cond.notify_all()

    async def mark_done(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self, from_idx: int = 0):
        while True:
            async with self._cond:
                while from_idx >= len(self.chunks):
                    if self.done:
                        return
                    await self._cond.wait()
                chunk = self.chunks[from_idx]
            from_idx += 1
            yield chunk


class _ChatStreamHub:
    """assistant_message_id → _ChatStream 映射表。"""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, _ChatStream] = {}
        self._lock = asyncio.Lock()

    async def register(self, msg_id: uuid.UUID) -> _ChatStream:
        async with self._lock:
            stream = _ChatStream()
            self._store[msg_id] = stream
            self._evict_stale_locked()
            return stream

    def get(self, msg_id: uuid.UUID) -> _ChatStream | None:
        return self._store.get(msg_id)

    def _evict_stale_locked(self) -> None:
        """清理 30 分钟前完成的流，避免内存泄漏。"""
        cutoff = time.monotonic() - 30 * 60
        stale = [mid for mid, s in self._store.items() if s.done and s.created_at < cutoff]
        for mid in stale:
            self._store.pop(mid, None)


CHAT_STREAM_HUB = _ChatStreamHub()


def _parse_sse_chunk(chunk: str | bytes) -> dict | None:
    """把 send_message_stream 产生的 ``data: {...}\\n\\n`` 解析回 dict。"""
    if isinstance(chunk, (bytes, bytearray)):
        chunk = chunk.decode("utf-8", errors="ignore")
    line = chunk.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _to_session_response(session: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        user_id=session.user_id,
        project_id=session.project_id,
        title=session.title,
        llm_config_id=session.llm_config_id,
        llm_config_name=session.llm_config.name if session.llm_config else None,
        system_prompt=session.system_prompt,
        message_count=len(session.messages) if session.messages else 0,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _to_message_response(msg: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        content=msg.content,
        tokens_used=msg.tokens_used,
        model_used=msg.model_used,
        meta_data=msg.meta_data,
        skill_invocation_id=msg.skill_invocation_id,
        created_at=msg.created_at,
    )


async def list_sessions(
    db: AsyncSession, user: User, project_id: uuid.UUID | None = None
) -> list[ChatSessionResponse]:
    query = (
        select(ChatSession)
        .options(selectinload(ChatSession.llm_config), selectinload(ChatSession.messages))
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    if project_id:
        query = query.where(ChatSession.project_id == project_id)
    result = await db.execute(query)
    sessions = list(result.scalars().unique().all())
    return [_to_session_response(s) for s in sessions]


async def create_session(
    db: AsyncSession, data: ChatSessionCreateRequest, user: User
) -> ChatSessionResponse:
    session = ChatSession(
        user_id=user.id,
        project_id=data.project_id,
        title=data.title or "新对话",
        llm_config_id=data.llm_config_id,
        system_prompt=data.system_prompt,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return _to_session_response(session)


async def get_session_detail(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> ChatSessionDetailResponse:
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)
    resp = _to_session_response(session)
    return ChatSessionDetailResponse(
        **resp.model_dump(),
        messages=[_to_message_response(m) for m in session.messages],
    )


async def update_session(
    db: AsyncSession, session_id: uuid.UUID, data: ChatSessionUpdateRequest, user: User
) -> ChatSessionResponse:
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)
    if data.title is not None:
        session.title = data.title
    if data.llm_config_id is not None:
        session.llm_config_id = data.llm_config_id
    if data.system_prompt is not None:
        session.system_prompt = data.system_prompt
    await db.flush()
    await db.refresh(session)
    return _to_session_response(session)


async def delete_session(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> None:
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)
    await db.delete(session)


async def get_session_messages(
    db: AsyncSession, session_id: uuid.UUID, user: User
) -> list[ChatMessageResponse]:
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)
    return [_to_message_response(m) for m in session.messages]


async def start_chat_task(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_content: str,
    user: User,
    llm_config_id_override: uuid.UUID | None = None,
) -> dict:
    """创建 user + 占位 assistant 消息，派发后台任务，立即返回消息 id。

    前端拿到 ``assistant_message_id`` 后，去 subscribe
    ``/chat/messages/{id}/stream`` 拉流。生成任务跑在独立的 asyncio task 里，
    不依赖请求连接；刷新/切页/切会话都不会中断它，重新 subscribe 同样的 id
    就能继续拿到后续事件 + 最终内容。
    """
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)

    # 先保存 user 消息 —— 即便后续 LLM 挂了，用户的提问也必须留在历史里。
    user_msg = ChatMessage(session_id=session.id, role="user", content=user_content)
    db.add(user_msg)
    await db.flush()

    # 占位 assistant 消息：content 先为空，meta_data.status = "streaming"。
    # orchestrator 会一边流式累积 content，一边 flush 回这条记录；最终 status
    # 改成 "completed" / "failed" / "interrupted"。
    assistant_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content="",
        meta_data={"status": "streaming"},
    )
    db.add(assistant_msg)
    await db.flush()

    if session.title == "新对话":
        session.title = (user_content[:50] or "新对话").strip() or "新对话"

    await db.commit()

    user_msg_id = user_msg.id
    assistant_msg_id = assistant_msg.id

    # 必须先 register hub，再派发 task —— 否则客户端可能抢先发 subscribe，
    # 拿到 None 然后走 DB 回放分支，错过实时事件。
    await CHAT_STREAM_HUB.register(assistant_msg_id)
    asyncio.create_task(
        _run_chat_task(
            session_id=session_id,
            user_content=user_content,
            user_id=user.id,
            llm_config_id_override=llm_config_id_override,
            assistant_msg_id=assistant_msg_id,
        )
    )

    return {
        "user_message_id": str(user_msg_id),
        "assistant_message_id": str(assistant_msg_id),
    }


async def _run_chat_task(
    *,
    session_id: uuid.UUID,
    user_content: str,
    user_id: uuid.UUID,
    llm_config_id_override: uuid.UUID | None,
    assistant_msg_id: uuid.UUID,
) -> None:
    """后台任务：独立 DB session 跑 LLM 流，事件广播到 hub + 定期落盘。"""
    stream = CHAT_STREAM_HUB.get(assistant_msg_id)
    if stream is None:
        stream = await CHAT_STREAM_HUB.register(assistant_msg_id)

    state = {
        "content": "",
        "reasoning": "",
        "meta": {"status": "streaming"},
        "infos": [],
        "tools": [],
        "model_used": None,
        "tokens_used": None,
    }

    FLUSH_EVERY_N_CHUNKS = 40
    delta_counter = 0

    async def persist() -> None:
        """把当前 state 回写到占位 assistant 消息。"""
        try:
            async with async_session_factory() as save_db:
                msg = await save_db.get(ChatMessage, assistant_msg_id)
                if msg is None:
                    return
                msg.content = state["content"]
                existing = dict(msg.meta_data or {})
                existing.update(state["meta"])
                if state["reasoning"]:
                    existing["reasoning"] = state["reasoning"]
                if state["tools"]:
                    existing["tools"] = state["tools"]
                if state["infos"]:
                    existing["infos"] = state["infos"]
                msg.meta_data = existing
                if state["model_used"]:
                    msg.model_used = state["model_used"]
                if state["tokens_used"]:
                    msg.tokens_used = state["tokens_used"]
                    # 回填到本次对话激活的 SkillUsageLog —— 历史链路这里全是
                    # NULL，导致"使用统计"页 avg_tokens 总是 ``—``。我们把
                    # 这一轮 chat 的累计 total_tokens 归到该 message 关联的
                    # 单条 SkillUsageLog（``execute_skill_invoke`` 已写
                    # message.skill_invocation_id）；这并不是"该 skill 单独
                    # 消耗"的精确值，但它是"用了该 skill 的整轮对话总开销"，
                    # 恰好是评估 skill 性价比 / 成本敏感度的可比指标。
                    skill_log_id = getattr(msg, "skill_invocation_id", None)
                    if skill_log_id is not None:
                        log = await save_db.get(SkillUsageLog, skill_log_id)
                        if log is not None:
                            log.tokens_consumed = state["tokens_used"]
                await save_db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to persist streaming chat message %s", assistant_msg_id
            )

    try:
        async with async_session_factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                raise RuntimeError("user not found for chat task")

            async for sse_chunk in send_message_stream(
                db,
                session_id,
                user_content,
                user,
                llm_config_id_override,
                assistant_message_id=assistant_msg_id,
                skip_persistence=True,
            ):
                event = _parse_sse_chunk(sse_chunk)
                if not event:
                    continue
                etype = event.get("type", "event")

                await stream.append(etype, event)

                if etype == "delta":
                    state["content"] += event.get("content", "") or ""
                    delta_counter += 1
                    if delta_counter % FLUSH_EVERY_N_CHUNKS == 0:
                        await persist()
                elif etype == "reasoning":
                    state["reasoning"] += event.get("content", "") or ""
                elif etype == "info":
                    msg_text = event.get("message", "") or ""
                    if msg_text:
                        state["infos"].append(msg_text)
                elif etype == "action":
                    # action 事件携带的 content 是"本意图的最终回答"，直接覆盖。
                    state["content"] = event.get("content", "") or state["content"]
                    action_meta = event.get("meta") or {}
                    if isinstance(action_meta, dict):
                        for k, v in action_meta.items():
                            state["meta"][k] = v
                elif etype == "error":
                    state["meta"]["status"] = "failed"
                    if not state["content"]:
                        state["content"] = f"❌ {event.get('message', '生成失败')}"
                elif etype == "usage":
                    # _handle_chat_stream 在 done 之前发的累计 token 数；只有
                    # 这条事件能让 orchestrator 在不写库的流式期间拿到 tokens。
                    tt = event.get("total_tokens")
                    if isinstance(tt, int) and tt > 0:
                        state["tokens_used"] = tt
                # done 事件由 finally 统一处理

        # 正常收尾：如果既没 error 也没 action 明确设置过状态，标记为完成。
        if state["meta"].get("status") == "streaming":
            state["meta"]["status"] = "completed"
        if not state["content"]:
            state["content"] = "（模型未输出任何内容，请稍后重试）"
            state["meta"].setdefault("status", "failed")
    except asyncio.CancelledError:
        # 与订阅端断开、服务 reload 等场景下的 asyncio 取消；避免打印整段 traceback，
        # 且给用户可读说明（而不是裸露的 CancelledError 字符串）。
        logger.warning(
            "Background chat task cancelled for message %s (partial len=%s)",
            assistant_msg_id,
            len(state.get("content") or ""),
        )
        state["meta"]["status"] = "interrupted"
        if not (state.get("content") or "").strip():
            state["content"] = (
                "（生成被中断。若您未点击「停止」，常见原因包括：页面刷新/关闭标签页、"
                "代理或网关断开长连接、或服务正在重启。请重试；"
                "生成过程中请尽量避免快速切换页面或反复切换模型。）"
            )
        try:
            await stream.append(
                "error",
                {"message": "生成任务已中断，请重试。"},
            )
        except Exception:  # noqa: BLE001
            pass
        await persist()
        try:
            await stream.append("done", {"assistant_message_id": str(assistant_msg_id)})
        except Exception:  # noqa: BLE001
            pass
        await stream.mark_done()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Background chat task failed for message %s", assistant_msg_id
        )
        state["meta"]["status"] = "failed"
        if not state["content"]:
            state["content"] = f"❌ 对话生成失败: {type(exc).__name__}: {exc}"
        try:
            await stream.append("error", {"message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        await persist()
        try:
            if not stream.done:
                await stream.append(
                    "done", {"assistant_message_id": str(assistant_msg_id)}
                )
        except Exception:  # noqa: BLE001
            pass
        await stream.mark_done()


async def subscribe_chat_stream(
    assistant_msg_id: uuid.UUID, user: User
) -> AsyncGenerator[str, None]:
    """SSE 订阅接口：实时回放 assistant 消息的事件流。

    - 若 hub 中存在 live stream：从 idx=0 开始重放已缓冲的事件 + 继续订阅新事件。
    - 若 hub 里没有（任务已完成 / 服务重启）：直接把 DB 里最终 content
      作为一次性 delta 吐出 + done。
    - 客户端刷新/切页后再次 subscribe 同一个 ``assistant_msg_id`` 也能无损续接。
    """
    async with async_session_factory() as db:
        msg = await db.get(ChatMessage, assistant_msg_id)
        if msg is None:
            yield _sse_error("消息不存在")
            yield _sse_done()
            return
        session = await db.get(ChatSession, msg.session_id)
        if session is None or session.user_id != user.id:
            yield _sse_error("无权访问该消息")
            yield _sse_done()
            return
        current_content = msg.content or ""
        current_meta = dict(msg.meta_data or {})

    stream = CHAT_STREAM_HUB.get(assistant_msg_id)
    if stream is None:
        # 任务已完成或服务重启 — 直接回放最终内容。
        if current_content:
            yield _sse_delta(current_content)
        status = current_meta.get("status")
        if status == "failed":
            yield _sse_error(current_content or "生成失败")
        yield _sse_done()
        return

    async for event_name, event_data in stream.subscribe():
        # event_data 本身就是 {"type": xxx, ...} 结构（start_chat_task 存进去的
        # 原样字典），直接 SSE 转发即可。
        if not isinstance(event_data, dict):
            continue
        if "type" not in event_data:
            event_data = {"type": event_name, **event_data}
        yield _sse(event_data)


async def send_message_stream(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_content: str,
    user: User,
    llm_config_id_override: uuid.UUID | None = None,
    *,
    web_search: bool = True,  # kept for backward compat, agent now always has tool access
    assistant_message_id: uuid.UUID | None = None,
    skip_persistence: bool = False,
) -> AsyncGenerator[str, None]:
    """发送用户消息并流式返回 AI 响应（SSE 格式 data: ...）。

    架构：真正的 agent tool-calling 循环。不再用"前端联网开关 + 后端 heuristic"
    决策检索，而是把 web_search 作为 OpenAI 兼容的 tool 直接暴露给模型，由模型
    自主决定何时调用、调用多少次。

    ``skip_persistence=True`` 时该 generator 只"发事件"、不"写数据库"——
    这是为了配合 ``_run_chat_task`` 的后台任务模式：占位消息已在 start_chat_task
    阶段建好，最终内容由 orchestrator 按事件累积再回写，这里若再写一遍会产生
    重复的 assistant 消息。
    """
    session = await _get_session_or_404(db, session_id)
    _check_owner(session, user)

    config_id = llm_config_id_override or session.llm_config_id
    if not config_id:
        config_id = await _get_default_config_id(db)
    if not config_id:
        yield _sse_error("未配置 LLM，请先在设置中添加 LLM 配置或为会话指定配置")
        yield _sse_done()
        return

    config = await _get_config_or_404(db, config_id)
    api_key = decrypt(config.api_key_encrypted) if config.api_key_encrypted else None

    # 根因修复（行锁卡死）：在进入 LLM 长流之前必须**清空主 db 上一切待写改动**
    # 并 commit。
    #
    # 历史故障：``session.llm_config_id = override`` 在后续任何一次 SELECT 触发
    # 隐式 flush 时会发出 ``UPDATE chat_sessions``，事务从此一直挂着；流式
    # 期间持续 6+ 分钟没有 commit，行锁不释放。后台 ``persist()`` 想 UPDATE
    # 同一会话的 ``chat_messages`` 时撞行锁 → ``wait_event=Lock/transactionid``
    # 死等到 PG idle-in-tx timeout，整个 chat 看起来就像后端整个挂掉。
    #
    # 修复策略：把所有"会话级元数据"的写入都提前 commit，让流式期间 ``db``
    # 只用于读（compose / get session 缓存等），不再持有任何 dirty 状态。
    config_changed = (
        llm_config_id_override is not None
        and llm_config_id_override != session.llm_config_id
    )
    if config_changed:
        session.llm_config_id = llm_config_id_override

    if not skip_persistence:
        user_msg = ChatMessage(session_id=session.id, role="user", content=user_content)
        db.add(user_msg)
        await db.flush()

    # 无论是否 skip_persistence，都要在 LLM 流之前 commit 一次：
    # - skip_persistence=True 的后台任务模式下，user_msg 已在 ``start_chat_task``
    #   持久化；这里 commit 主要是把 ``session.llm_config_id`` 的 UPDATE 落地、
    #   让事务收尾、行锁释放；
    # - skip_persistence=False 的同步直流模式下，commit 同时让用户消息也持久化，
    #   避免 SSE 被刷新打断时丢失提问。
    if config_changed or not skip_persistence:
        await db.commit()

    intent = detect_intent(user_content)

    if intent.intent == IntentType.REVIEW and session.project_id:
        async for chunk in _handle_review_intent(
            db, session, intent, user, config, api_key,
            skip_persistence=skip_persistence,
        ):
            yield chunk
        return

    if intent.intent == IntentType.GENERATE_TESTCASES and session.project_id:
        async for chunk in _handle_generate_intent(
            db, session, intent, user, config, api_key,
            skip_persistence=skip_persistence,
        ):
            yield chunk
        return

    async for chunk in _handle_chat_stream(
        db,
        session,
        user_content,
        user,
        config,
        api_key,
        assistant_message_id=assistant_message_id,
        skip_persistence=skip_persistence,
    ):
        yield chunk


# OpenClaw / 技能包常见路径：skill_*__invoke → http_get/post_json（可多轮）→ 可能需要
# web_search 辅助 → 最终作答。3 轮在真实场景中极易触顶导致「卡住感」或空答复。
MAX_TOOL_ITERATIONS = 8


async def _handle_review_intent(
    db: AsyncSession,
    session: ChatSession,
    intent: DetectedIntent,
    user: "User",
    config: LLMConfig,
    api_key: str | None,
    *,
    skip_persistence: bool = False,
) -> AsyncGenerator[str, None]:
    """Execute a document review and yield SSE events."""
    from app.modules.requirements.review_service import trigger_review

    doc = await resolve_document(
        db, session.project_id, intent.params.get("doc_hint")
    )
    if not doc:
        error_content = "❌ 当前项目下没有已解析的需求文档，请先上传文档后再评审。"
        meta = build_action_metadata(IntentType.REVIEW, error="no_document")
        if not skip_persistence:
            _save_assistant_msg(db, session, error_content, meta_data=meta)
            await db.flush()
        yield _sse_action(error_content, meta)
        yield _sse_done()
        return

    yield _sse_info(f"正在评审文档「{doc.filename}」，请稍候")

    try:
        review_resp = trigger_review(db, doc.id, user, config.id)
        review = await review_resp

        review_data = {
            "overall_score": review.overall_score,
            "summary": review.summary,
            "dimensions": review.dimensions,
            "issues": review.issues,
        }
        content = format_review_result(review_data)
        meta = build_action_metadata(
            IntentType.REVIEW,
            review_id=str(review.id),
            review_data=review_data,
            document_name=doc.filename,
        )

        if not skip_persistence:
            _save_assistant_msg(db, session, content, model_used=config.model, meta_data=meta)
            await db.flush()

        yield _sse_action(content, meta)
        yield _sse_done()

    except Exception as e:
        logger.exception("Review via chat failed for doc %s", doc.id)
        error_content = f"❌ 评审失败: {type(e).__name__}: {e}"
        meta = build_action_metadata(
            IntentType.REVIEW, error=str(e), document_name=doc.filename,
        )
        if not skip_persistence:
            _save_assistant_msg(db, session, error_content, meta_data=meta)
            await db.flush()
        yield _sse_error(error_content)
        yield _sse_done()


async def _handle_generate_intent(
    db: AsyncSession,
    session: ChatSession,
    intent: DetectedIntent,
    user: "User",
    config: LLMConfig,
    api_key: str | None,
    *,
    skip_persistence: bool = False,
) -> AsyncGenerator[str, None]:
    """Execute testcase generation and yield SSE events with streamed deltas."""
    from app.modules.testcases.generation_service import (
        STREAM_HUB,
        start_generation_batch,
    )
    from app.modules.testcases.schemas import GenerateRequest

    def _maybe_save(content: str, meta: dict) -> None:
        if not skip_persistence:
            _save_assistant_msg(db, session, content, model_used=config.model, meta_data=meta)

    doc = await resolve_document(
        db, session.project_id, intent.params.get("doc_hint")
    )
    if not doc:
        error_content = "❌ 当前项目下没有已解析的需求文档，请先上传文档后再生成用例。"
        meta = build_action_metadata(
            IntentType.GENERATE_TESTCASES, error="no_document",
        )
        _maybe_save(error_content, meta)
        if not skip_persistence:
            await db.flush()
        yield _sse_action(error_content, meta)
        yield _sse_done()
        return

    yield _sse_info(f"正在根据文档「{doc.filename}」生成测试用例")

    gen_request = GenerateRequest(document_id=doc.id, llm_config_id=config.id)
    batch_info = await start_generation_batch(db, session.project_id, gen_request, user)
    batch_id = str(batch_info.id)

    stream = STREAM_HUB.get(batch_info.id)
    testcases: list[dict] = []

    if stream is not None:
        async for event_name, event_data in stream.subscribe():
            if event_name == "delta":
                piece = event_data.get("content", "")
                if piece:
                    yield _sse_delta(piece)
            elif event_name == "info":
                msg = event_data.get("message", "")
                if msg:
                    yield _sse_info(msg)
            elif event_name == "generated":
                testcases = event_data.get("testcases") or []
                break
            elif event_name == "error":
                error_content = f"❌ 生成失败: {event_data.get('message', '未知错误')}"
                meta = build_action_metadata(
                    IntentType.GENERATE_TESTCASES,
                    error=event_data.get("message"),
                    document_name=doc.filename,
                )
                _maybe_save(error_content, meta)
                if not skip_persistence:
                    await db.flush()
                yield _sse_action(error_content, meta)
                yield _sse_done()
                return

    if testcases:
        content = format_generation_result(testcases, batch_id)
        meta = build_action_metadata(
            IntentType.GENERATE_TESTCASES,
            batch_id=batch_id,
            testcases=testcases,
            document_name=doc.filename,
        )
        _maybe_save(content, meta)
        if not skip_persistence:
            await db.flush()
        yield _sse_action(content, meta)
    else:
        error_content = "❌ 未能解析到有效的测试用例，请检查文档内容或重试。"
        meta = build_action_metadata(
            IntentType.GENERATE_TESTCASES, error="no_testcases",
            document_name=doc.filename,
        )
        _maybe_save(error_content, meta)
        if not skip_persistence:
            await db.flush()
        yield _sse_action(error_content, meta)

    yield _sse_done()


def merge_skill_context_into_openai_messages(
    openai_messages: list[dict],
    skill_ctx: SkillContext,
) -> list[dict]:
    """把 SkillRouter 的 system_messages 插在所有前置 system 块之后、history 之前。"""
    if not skill_ctx.system_messages:
        return openai_messages
    i = 0
    while i < len(openai_messages) and openai_messages[i].get("role") == "system":
        i += 1
    return openai_messages[:i] + skill_ctx.system_messages + openai_messages[i:]


def tools_for_chat_session(skill_ctx: SkillContext) -> list[dict]:
    """空 SkillContext 时必须返回 ``TOOLS`` 原对象引用（零侵入契约）。"""
    return TOOLS if not skill_ctx.candidate_tools else TOOLS + skill_ctx.candidate_tools


async def _handle_chat_stream(
    db: AsyncSession,
    session: ChatSession,
    user_content: str,
    user: User,
    config: LLMConfig,
    api_key: str | None,
    *,
    assistant_message_id: uuid.UUID | None = None,
    skip_persistence: bool = False,
) -> AsyncGenerator[str, None]:
    """Agent 对话流：OpenAI tool-calling 循环。

    流程：
      1. 把 web_search 作为 tool 暴露给模型；
      2. 流式读取一轮响应；若模型发出 tool_calls，执行工具后把结果塞回历史，
         再次调用模型；最多迭代 ``MAX_TOOL_ITERATIONS`` 次；
      3. 期间的 delta/reasoning 原样透传前端；工具调用以 info 事件显式告知。
    """
    skill_ctx = await compose(db, session.project_id, session, user_content)
    openai_messages = merge_skill_context_into_openai_messages(
        _build_context(session, user_content),
        skill_ctx,
    )
    tools_for_chat = tools_for_chat_session(skill_ctx)

    # Task 12.6：在 LLM 第一轮请求前把 always / manual / trigger 命中的 skill
    # 推给前端，让 SkillActivationHint banner 立即显示"已自动激活"。
    # agent_callable 候选不在此处推——它要等模型真的调用 skill_*__invoke 时
    # 才"真正激活"，那时由 ChatMessage.skill_invocation_id 体现到消息徽章上。
    for info in skill_ctx.activated_skills:
        yield _sse_skill_activated({
            "skill_id": str(info.skill_id),
            "slug": info.slug,
            "name": info.name,
            "activation_reason": info.activation_reason,
            "matched_trigger": info.matched_trigger,
        })

    full_content = ""
    full_reasoning = ""
    tool_trace: list[dict] = []
    model_used = config.model
    usage_total = None
    try:
        async with chat_platform_runtime_cm(
            db,
            user,
            session.project_id,
            session.llm_config_id,
            assistant_message_id,
        ):
            for iteration in range(MAX_TOOL_ITERATIONS):
                round_content = ""
                round_reasoning = ""
                pending_tool_calls: dict[int, dict] = {}
                finish_reason: str | None = None
                last_chunk = None

                # On the final iteration, force the model to produce a text answer
                # rather than another tool call. We keep ``tools=`` declared so the
                # assistant→tool message history in the prompt remains valid, but
                # set ``tool_choice="none"`` — OpenAI-canonical way of saying
                # "no more tool calls, finalize your answer now".
                is_last = iteration == MAX_TOOL_ITERATIONS - 1
                tool_choice: str | None = None
                if is_last and iteration > 0:
                    tool_choice = "none"
                    openai_messages.append({
                        "role": "user",
                        "content": (
                            "请立即基于上面 tool 返回的内容，用中文 Markdown 直接回答我的问题。"
                            "若刚调用过 skill_*__invoke 且文档要求请求 HTTP 接口，你必须已经用 "
                            "http_get_json 或 http_post_json 拿到真实响应再整理；"
                            "禁止仅根据 SKILL 文档文字臆造「查询结果」。\n"
                            "即使信息不完整，也必须列出已知关键数据，并说明缺少哪些部分。"
                            "不要再调用任何工具。"
                        ),
                    })

                async for chunk in stream_chat(
                    provider=config.provider,
                    model=config.model,
                    messages=openai_messages,
                    api_key=api_key,
                    base_url=config.base_url,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    tools=tools_for_chat,
                    tool_choice=tool_choice,
                ):
                    last_chunk = chunk
                    if chunk.model:
                        model_used = chunk.model
                    if not chunk.choices:
                        continue

                    choice = chunk.choices[0]
                    delta = choice.delta

                    reasoning_piece = getattr(delta, "reasoning_content", None)
                    if reasoning_piece:
                        round_reasoning += reasoning_piece
                        yield _sse_reasoning(reasoning_piece)

                    content_piece = getattr(delta, "content", None)
                    if content_piece:
                        round_content += content_piece
                        yield _sse_delta(content_piece)

                    for tc in (delta.tool_calls or []):
                        slot = pending_tool_calls.setdefault(
                            tc.index,
                            {"id": None, "name": "", "arguments": ""},
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["arguments"] += fn.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                full_content += round_content
                full_reasoning += round_reasoning
                # 多轮工具调用每轮都会拿到自己这一轮的 total_tokens；要做"本次
                # 对话总开销"必须累加，而不是覆盖（覆盖只会留下最后一轮，前几轮
                # 的工具回灌成本被吃掉）。
                if last_chunk is not None and getattr(last_chunk, "usage", None):
                    round_usage = getattr(last_chunk.usage, "total_tokens", None)
                    if isinstance(round_usage, int) and round_usage > 0:
                        usage_total = (usage_total or 0) + round_usage

                # If this is the forced-answer round, never execute another tool
                # call even if the model tried to emit one — the gateway may not
                # honor ``tool_choice=none``, so we enforce it client-side. We do
                # NOT promote reasoning to content here; reasoning is private
                # chain-of-thought, not the final answer. The outer post-loop
                # fallback writes a clean "no answer" banner instead.
                if is_last:
                    break

                if finish_reason != "tool_calls" or not pending_tool_calls:
                    break

                # Append assistant message with tool_calls to the history so the
                # tool results below can reference them (OpenAI protocol requirement).
                tc_list = []
                for idx in sorted(pending_tool_calls.keys()):
                    item = pending_tool_calls[idx]
                    tc_list.append({
                        "id": item["id"] or f"call_{iteration}_{idx}",
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"] or "{}",
                        },
                    })
                assistant_turn: dict = {
                    "role": "assistant",
                    "content": round_content or None,
                    "tool_calls": tc_list,
                }
                # 思维链回填：火山方舟 / 智谱 GLM 的 thinking 模式契约要求
                # 上一轮的 ``reasoning_content`` 必须随下一轮 assistant message 一并回传
                # （否则 400 ``The reasoning_content in the thinking mode must be passed
                # back to the API``）。OpenAI 等标准接口会忽略未识别字段，无害。
                if round_reasoning:
                    assistant_turn["reasoning_content"] = round_reasoning
                openai_messages.append(assistant_turn)

                for tc in tc_list:
                    name = tc["function"]["name"]
                    args_raw = tc["function"]["arguments"]
                    args_preview = ""
                    sources_preview = ""
                    try:
                        parsed_args = json.loads(args_raw) if args_raw else {}
                        if isinstance(parsed_args, dict):
                            args_preview = str(parsed_args.get("query") or "")
                            srcs = parsed_args.get("sources")
                            if isinstance(srcs, list) and srcs:
                                sources_preview = "，源：" + "/".join(str(s) for s in srcs[:5])
                    except Exception:  # noqa: BLE001
                        args_preview = args_raw[:80]
                    yield _sse_info(f"🔎 {name}：{args_preview}{sources_preview}")
                    result_json = await safe_run_tool(
                        db,
                        name,
                        args_raw,
                        active_system_skill_slugs=skill_ctx.active_system_skill_slugs,
                        skill_id_by_tool_name=skill_ctx.skill_id_by_tool_name,
                        allowed_platform_tools=skill_ctx.allowed_platform_tools,
                        session_id=session.id,
                        project_id=session.project_id,
                        assistant_message_id=assistant_message_id,
                        allowed_http_hosts=skill_ctx.allowed_http_hosts,
                    )
                    # 如果 tool 返回里带 sources_used，顺手 emit 给前端看实际命中哪几家
                    try:
                        payload = json.loads(result_json)
                        used = payload.get("sources_used") if isinstance(payload, dict) else None
                        if isinstance(used, list) and used:
                            yield _sse_info(
                                "✓ 命中来源：" + "、".join(str(x) for x in used[:8])
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_json,
                    })
                    tool_trace.append({"name": name, "arguments": args_raw, "result": result_json[:4000]})
                yield _sse_info("整合工具结果中…")
                # Continue loop: model will now see tool outputs and keep responding.

            if not full_content:
                # Model didn't commit to an answer (only reasoning, or totally empty).
                # Never auto-promote reasoning to content — that's how users end up
                # seeing raw thinking tokens as if they were the final reply. Emit a
                # clean fallback message instead; reasoning is still persisted in
                # meta_data (below) for debugging.
                fallback = (
                    "（模型未能在本轮给出直接答案。可能是工具结果不足或模型仍在思考，"
                    "请稍等几秒后重新提问，或换一个更具体的提问方式。）"
                )
                full_content = fallback
                yield _sse_delta(fallback)

            meta: dict | None = None
            if full_reasoning or tool_trace:
                meta = {}
                if full_reasoning:
                    meta["reasoning"] = full_reasoning
                if tool_trace:
                    meta["tools"] = tool_trace

            if not skip_persistence:
                assistant_msg = ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=full_content,
                    model_used=model_used,
                    tokens_used=usage_total,
                    meta_data=meta,
                )
                db.add(assistant_msg)

                if session.title == "新对话" and len(session.messages) <= 1:
                    session.title = (user_content[:50] or "新对话").strip() or "新对话"

                await db.flush()

            # 不论同步直流还是后台任务模式，都在 done 之前把累计 tokens 抛给
            # 上层（同步模式下没人订阅也无害）。后台任务模式下 orchestrator
            # 据此填 ChatMessage.tokens_used + SkillUsageLog.tokens_consumed。
            if isinstance(usage_total, int) and usage_total > 0:
                yield _sse_usage(usage_total)
            yield _sse_done()

    except (asyncio.CancelledError, GeneratorExit):
        # 后台任务模式下（skip_persistence=True），orchestrator 会在自己的
        # finally 里完成最终持久化；这里不要再起独立 session 保存一次，否则
        # 会产生重复的 assistant 消息 + "已中断"提示。
        if skip_persistence:
            raise
        logger.info("Chat stream cancelled for session %s; persisting partial output", session.id)
        try:
            await asyncio.shield(
                _persist_interrupted_message(
                    session_id=session.id,
                    content=full_content,
                    reasoning=full_reasoning,
                    model_used=model_used,
                )
            )
        except asyncio.CancelledError:
            logger.warning(
                "Shielded interrupt-save was cancelled for session %s", session.id
            )
        raise
    except Exception as e:
        logger.exception("Chat stream failed for session %s", session.id)
        if not skip_persistence and full_content:
            db.add(
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=full_content,
                    model_used=model_used,
                    meta_data={"reasoning": full_reasoning} if full_reasoning else None,
                )
            )
            await db.flush()
        yield _sse_error(f"生成中断: {type(e).__name__}: {e}")
        yield _sse_done()


async def _persist_interrupted_message(
    *,
    session_id: uuid.UUID,
    content: str,
    reasoning: str,
    model_used: str | None,
) -> None:
    """Write the interrupted assistant reply on a fresh DB session.

    The caller's request session might already be rolling back, so we must open
    our own session to guarantee the write lands.

    Content rules (critical for UX):
    - If the model has emitted *any* real content, save that real content + a
      small "(本轮被中断)" tail so the user knows it is partial. **Never replace
      content with reasoning** — users complained about seeing raw thinking
      tokens mistaken for the final answer after refresh.
    - If only reasoning was emitted so far, show a concise interrupt banner
      (not the reasoning) and stash the reasoning in meta_data for debug only.
    - If nothing was emitted, show a plain "interrupted in thinking" banner.
    """
    if content.strip():
        final_content = content.rstrip() + "\n\n> ⚠️ 本轮回答被中断，以上为已生成的部分内容。"
    elif reasoning.strip():
        final_content = (
            "本轮回答在模型思考阶段被中断，暂未生成正式答复。\n\n"
            "> 提示：模型仍在规划/检索中就收到断开信号（通常是页面刷新或切换），"
            "请重新提问即可。"
        )
    else:
        final_content = "本轮回答被中断，请重新提问。"

    meta_data: dict = {"interrupted": True}
    if reasoning:
        # Reasoning is kept ONLY in meta for debugging / future UI — it must
        # not become the user-facing message.
        meta_data["reasoning"] = reasoning
    if not content.strip() and not reasoning.strip():
        meta_data["empty_interrupted"] = True

    try:
        async with async_session_factory() as recover_db:
            recover_db.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=final_content,
                    model_used=model_used,
                    meta_data=meta_data,
                )
            )
            await recover_db.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to persist interrupted message for session %s", session_id
        )


def _save_assistant_msg(
    db: AsyncSession,
    session: ChatSession,
    content: str,
    *,
    model_used: str | None = None,
    meta_data: dict | None = None,
) -> ChatMessage:
    """Create and add a new assistant message to the session."""
    msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content,
        model_used=model_used,
        meta_data=meta_data,
    )
    db.add(msg)

    if session.title == "新对话" and len(session.messages) <= 1:
        action_titles = {
            "review": "评审需求文档",
            "generate_testcases": "生成测试用例",
        }
        action_type = (meta_data or {}).get("action_type")
        session.title = action_titles.get(action_type, content[:50])

    return msg


def _build_context(
    session: ChatSession,
    user_content: str,
) -> list[dict]:
    """构造发送给 LLM 的 messages 列表。

    注入顺序：
      1. 用户/项目自定义 system prompt（可选）
      2. Agent 行为守则——已经在守则正文顶部内嵌"当前时间/年份/星期"，
         强制模型按当前日期作答 + 强制带年份+recency 调用 web_search
      3. 历史对话
      4. 本轮用户消息

    历史教训：以前把"运行环境/当前时间"作为单独 system message 放在守则之后，
    模型经常忽略边缘消息，按训练截止时知道的旧赛季/旧年份去构造搜索 query
    （比如问"今天 CBA 赛程"会去查 2024 赛事）。把日期融合进守则正文后这种
    情况大幅减少。
    """
    messages: list[dict] = []
    if session.system_prompt:
        messages.append({"role": "system", "content": session.system_prompt})

    # 每次会话开头都重新生成（拿到"现在"这一刻的日期），不能缓存为常量
    messages.append({"role": "system", "content": build_agent_system_guidance()})

    for msg in session.messages:
        if msg.role in ("user", "assistant"):
            messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": user_content})
    return messages


async def _get_session_or_404(db: AsyncSession, session_id: uuid.UUID) -> ChatSession:
    stmt = (
        select(ChatSession)
        .options(
            selectinload(ChatSession.messages),
            selectinload(ChatSession.llm_config),
        )
        .where(ChatSession.id == session_id)
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        raise NotFoundException("会话不存在")
    return session


async def _get_config_or_404(db: AsyncSession, config_id: uuid.UUID) -> LLMConfig:
    result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise NotFoundException("LLM 配置不存在")
    return config


async def _get_default_config_id(db: AsyncSession) -> uuid.UUID | None:
    result = await db.execute(
        select(LLMConfig.id).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    row = result.scalar_one_or_none()
    return row


def _check_owner(session: ChatSession, user: User) -> None:
    if session.user_id != user.id and not user.is_superuser:
        raise PermissionDeniedException("无权访问此会话")
