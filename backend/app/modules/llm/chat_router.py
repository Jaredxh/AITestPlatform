import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.exceptions import AppException
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.llm.chat_service import (
    create_session,
    delete_session,
    get_pending_task_summary,
    get_session_detail,
    get_session_messages,
    list_sessions,
    send_message_stream,
    start_chat_task,
    subscribe_chat_stream,
    subscribe_session_system_events,
    update_session,
)
from app.modules.llm.file_handler import process_upload
from app.modules.llm.schemas import (
    ChatSendRequest,
    ChatSessionCreateRequest,
    ChatSessionUpdateRequest,
)

router = APIRouter(prefix="/api/chat", tags=["AI 对话"])


@router.get("/sessions", response_model=dict)
async def list_chat_sessions(
    project_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions = await list_sessions(db, current_user, project_id)
    return success_response(data=[s.model_dump(mode="json") for s in sessions])


@router.post("/sessions", response_model=dict)
async def create_chat_session(
    data: ChatSessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await create_session(db, data, current_user)
    return success_response(data=session.model_dump(mode="json"), message="会话已创建")


@router.get("/sessions/{session_id}", response_model=dict)
async def get_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    detail = await get_session_detail(db, session_id, current_user)
    return success_response(data=detail.model_dump(mode="json"))


@router.patch("/sessions/{session_id}", response_model=dict)
async def patch_chat_session(
    session_id: uuid.UUID,
    data: ChatSessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await update_session(db, session_id, data, current_user)
    return success_response(data=session.model_dump(mode="json"), message="会话已更新")


@router.delete("/sessions/{session_id}", response_model=dict)
async def remove_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await delete_session(db, session_id, current_user)
    return success_response(message="会话已删除")


@router.get("/sessions/{session_id}/messages", response_model=dict)
async def get_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    messages = await get_session_messages(db, session_id, current_user)
    return success_response(data=[m.model_dump(mode="json") for m in messages])


@router.post("/sessions/{session_id}/send")
async def send_message(
    session_id: uuid.UUID,
    data: ChatSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """异步发起一轮对话：立刻返回 user_message_id + assistant_message_id，
    实际 LLM 生成跑在后台任务里；前端拿到 assistant_message_id 后去 subscribe
    ``/messages/{id}/stream`` 拉 SSE。刷新/切页都不会中断后台任务。"""
    ids = await start_chat_task(
        db,
        session_id,
        data.content,
        current_user,
        data.llm_config_id,
    )
    return success_response(data=ids, message="已发起对话任务")


@router.get("/messages/{message_id}/stream")
async def stream_message(
    message_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """SSE 订阅单条 assistant 消息的生成过程：
    - 进行中：实时把 delta / reasoning / info / action / done 事件推给客户端；
    - 已完成：一次性回放最终 content 再 done；
    - 客户端无论何时 subscribe、subscribe 多少次，事件序列都是一致的。
    """

    async def event_generator():
        async for chunk in subscribe_chat_stream(message_id, current_user):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/send-sse")
async def send_message_sse_legacy(
    session_id: uuid.UUID,
    data: ChatSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """旧版同步 SSE 接口（保留以兼容未升级客户端）：请求生命周期内流式返回；
    连接断开 = 任务中断。新前端请使用 POST /send + GET /messages/{id}/stream
    的两步式调用。"""

    async def event_generator():
        async for chunk in send_message_stream(
            db,
            session_id,
            data.content,
            current_user,
            data.llm_config_id,
            web_search=data.web_search,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/upload", response_model=dict)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """上传文件并提取内容（Word/PDF 提取文本，图片转 base64）。"""
    try:
        result = await process_upload(file)
    except ValueError as e:
        raise AppException(str(e), code="FILE_ERROR")
    return success_response(data=result)


# ─────────────── Phase 13 / Task 13.3 — 系统事件 SSE + 离线汇总 ───────────────


@router.get("/sessions/{session_id}/system-events")
async def stream_session_system_events(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """SSE 订阅会话级系统事件（skill_card / task_status / execution_event）。

    前端打开 chat 视图时建立长连接；与 ``/messages/{id}/stream`` 解耦——后者跟
    随单条 assistant 消息生命周期，本流跟随整个 session。事件类型见
    ``system_event_service.publish_*``。
    """

    async def event_generator():
        async for chunk in subscribe_session_system_events(session_id, current_user):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/pending-task-summary", response_model=dict)
async def session_pending_task_summary(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """首屏顶部 "你离开期间完成 N 个任务" 汇总卡的数据源（最近 20 条 execution_event）。"""
    summary = await get_pending_task_summary(db, session_id, current_user)
    return success_response(data=summary)
