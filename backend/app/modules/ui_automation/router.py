"""UI 自动化模块的 HTTP 路由。

URL 设计：
- ``GET   /api/projects/{project_id}/ui-environments``        list（嵌套在 project 下）
- ``POST  /api/projects/{project_id}/ui-environments``        create
- ``GET   /api/ui-environments/{env_id}``                     detail（含 preconditions）
- ``PATCH /api/ui-environments/{env_id}``                     update
- ``DELETE /api/ui-environments/{env_id}``                    delete
- ``POST  /api/ui-environments/{env_id}/clear-state``         清掉 storage_state 文件

- ``GET   /api/ui-environments/{env_id}/preconditions``        list
- ``POST  /api/ui-environments/{env_id}/preconditions``        create
- ``PATCH /api/ui-preconditions/{precondition_id}``            update（用 prec_id 单挂）
- ``DELETE /api/ui-preconditions/{precondition_id}``           delete

权限模型：
- 写操作（create / edit / delete / clear-state）按 ``UI_ENV_*`` 系列 4 个权限拆
- 前置步骤的写复用 ``UI_ENV_EDIT``（编辑环境的人才能管前置步骤）
- 读操作统一 ``UI_ENV_VIEW``
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundException
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.ui_automation.execution_service import (
    continue_debug_execution,
    delete_execution,
    get_execution_detail,
    get_execution_or_404,
    get_recent_config,
    get_step_or_404,
    list_executions,
    preflight_modules,
    retry_failed_execution,
    start_execution,
    stop_execution,
    subscribe_execution_stream,
)
from app.modules.ui_automation.replayer import replay as replay_execution
from app.modules.ui_automation.schemas import (
    ExecutionCreateRequest,
    ExecutionRetryRequest,
    PreconditionTemplateCreateRequest,
    PreconditionTemplateUpdateRequest,
    PreflightModulesRequest,
    TestEnvironmentCreateRequest,
    TestEnvironmentUpdateRequest,
    TestPreconditionRequest,
)
from app.modules.ui_automation.service import (
    clear_environment_state,
    create_environment,
    create_precondition,
    delete_environment,
    delete_precondition,
    get_environment_detail,
    list_environments,
    list_preconditions,
    test_precondition,
    update_environment,
    update_precondition,
)

router = APIRouter(tags=["UI 自动化 - 环境配置"])


# ─── 环境（嵌在 project 下：list / create）────────────────────────────


@router.get("/api/projects/{project_id}/ui-environments")
async def list_envs(
    project_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_VIEW)),
):
    items, total = await list_environments(db, project_id, user, page=page, page_size=page_size)
    return success_response(data={
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/api/projects/{project_id}/ui-environments")
async def create_env(
    project_id: uuid.UUID,
    data: TestEnvironmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_CREATE)),
):
    detail = await create_environment(db, project_id, data, user)
    return success_response(data=detail.model_dump(mode="json"), message="测试环境已创建")


# ─── 环境（按 env_id 单挂：get / update / delete / clear-state）──────


@router.get("/api/ui-environments/{env_id}")
async def get_env(
    env_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_VIEW)),
):
    detail = await get_environment_detail(db, env_id, user)
    return success_response(data=detail.model_dump(mode="json"))


@router.patch("/api/ui-environments/{env_id}")
async def patch_env(
    env_id: uuid.UUID,
    data: TestEnvironmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    detail = await update_environment(db, env_id, data, user)
    return success_response(data=detail.model_dump(mode="json"))


@router.delete("/api/ui-environments/{env_id}")
async def delete_env(
    env_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_DELETE)),
):
    await delete_environment(db, env_id, user)
    return success_response(message="测试环境已删除")


@router.post("/api/ui-environments/{env_id}/clear-state")
async def post_clear_state(
    env_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    """删除该环境的 storage_state 文件并清空 ``state_saved_at``。

    场景：登录态过期 / 测试账号换密码后想强制下一次 execution 重新跑登录。
    幂等：文件不存在时返回 ``state_file_existed=false``，不报错。
    """
    result = await clear_environment_state(db, env_id, user)
    return success_response(
        data=result.model_dump(mode="json"),
        message=("storage_state 已清除" if result.state_file_removed
                 else "storage_state 文件本来就不存在"),
    )


# ─── 前置步骤 ────────────────────────────────────────────────────────


@router.get("/api/ui-environments/{env_id}/preconditions")
async def list_precs(
    env_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_VIEW)),
):
    items = await list_preconditions(db, env_id, user)
    return success_response(data={"items": [item.model_dump(mode="json") for item in items]})


@router.post("/api/ui-environments/{env_id}/preconditions")
async def create_prec(
    env_id: uuid.UUID,
    data: PreconditionTemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    item = await create_precondition(db, env_id, data, user)
    return success_response(data=item.model_dump(mode="json"), message="前置步骤已创建")


@router.patch("/api/ui-preconditions/{precondition_id}")
async def patch_prec(
    precondition_id: uuid.UUID,
    data: PreconditionTemplateUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    item = await update_precondition(db, precondition_id, data, user)
    return success_response(data=item.model_dump(mode="json"))


@router.delete("/api/ui-preconditions/{precondition_id}")
async def delete_prec(
    precondition_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    await delete_precondition(db, precondition_id, user)
    return success_response(message="前置步骤已删除")


# ─── Task 9.3：执行配置弹窗 — 复用上次配置 ──────────────────────────


@router.get("/api/projects/{project_id}/recent-executions/last-config")
async def get_last_execution_config(
    project_id: uuid.UUID,
    testcase_ids: Annotated[
        list[uuid.UUID] | None,
        Query(description="用例组合：完全匹配的最近一次执行配置；省略则返回最近一次任意配置"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """返回上一次执行某用例组合时使用的配置（弹窗"复用上次"按钮用）。

    Task 10.1 起真正查库：在 ``ui_executions`` 里按 ``created_at desc`` 找
    第一条 ``config_snapshot.testcase_ids`` 与 ``testcase_ids`` **集合相等**
    （顺序无关）的执行；找不到再降级返回该项目最近一次任意配置。

    返回结构：``{"config": {...} | null}``。``null`` 表示从未跑过——前端
    "复用上次"按钮置灰即可。
    """
    config = await get_recent_config(
        db, project_id, user, testcase_ids=testcase_ids,
    )
    return success_response(data={"config": config})


# ─── Task 9.6：执行 API + SSE + 停止/重试 ────────────────────────────


@router.post("/api/projects/{project_id}/ui-executions")
async def create_execution(
    project_id: uuid.UUID,
    data: ExecutionCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_RUN)),
):
    """创建并立即派发一次 UI 自动化执行。

    返回 ``{id, status: "pending", ...}``；前端拿 ``id`` 去
    ``GET /api/ui-executions/{id}/stream`` 订阅 SSE 进度流。
    """
    item = await start_execution(db, project_id, data, user)
    return success_response(data=item.model_dump(mode="json"), message="执行已派发")


@router.post("/api/projects/{project_id}/ui-executions/preflight-modules")
async def preflight_modules_endpoint(
    project_id: uuid.UUID,
    data: PreflightModulesRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_RUN)),
):
    """执行配置弹窗"测试地址"区段的数据来源。

    给定一组 testcase_ids，返回它们涉及的所有模块 + 当前 entry_path。
    前端用此结果渲染"模块入口路径"列表，允许用户在执行前临时覆盖。

    与 ``preview-merge`` / ``missing-check`` 同属"提交前预览"系列。
    """
    result = await preflight_modules(db, project_id, data, user)
    return success_response(data=result.model_dump(mode="json"))


@router.get("/api/projects/{project_id}/ui-executions")
async def list_project_executions(
    project_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Annotated[
        str | None,
        Query(description="按 status 过滤；如 running / completed / failed 等"),
    ] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    items, total = await list_executions(
        db, project_id, user, page=page, page_size=page_size, status=status,
    )
    return success_response(data={
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/api/ui-executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    detail = await get_execution_detail(db, execution_id, user)
    return success_response(data=detail.model_dump(mode="json"))


@router.get("/api/ui-executions/{execution_id}/stream")
async def stream_execution(
    execution_id: uuid.UUID,
    user: User = Depends(get_current_user),
):
    """SSE 订阅一次执行的事件流。

    与一期 ``GET /chat/messages/{id}/stream`` 走同一套 SSE 协议（``data: <json>\\n\\n``）。
    刷新页面 / 切换 tab / 重连同样的 ``execution_id`` 都能拿到完整事件序列
    （hub 里有就实时回放，没有就发一个 done 让前端去拉详情）。

    权限走 ``ui_exec:view``——但 SSE 端点不能用 ``Depends(get_db)`` 拿 session
    （SSE 长连接里 db session 不能跨 yield 用），所以 service 层自己开 session
    做权限校验。
    """
    # 显式做权限检查；不能用 require_permission 装饰器因为它链了 get_db。
    if not (user.is_superuser or user.has_permission(Permissions.UI_EXEC_VIEW)):
        raise NotFoundException("执行记录不存在")

    async def event_generator():
        async for chunk in subscribe_execution_stream(execution_id, user):
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


@router.post("/api/ui-executions/{execution_id}/stop")
async def post_stop_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_STOP)),
):
    """请求停止一次执行（幂等）。

    实际中止时机：Engine 在每条用例之间会 ``await is_execution_stopped`` 自然退出。
    本端点只把 status 写成 ``stopped``，最坏情况下当前 case 跑完再退；不强杀线程。
    """
    result = await stop_execution(db, execution_id, user)
    msg = "执行已请求停止" if not result.already_terminal else "执行已是终态"
    return success_response(data=result.model_dump(mode="json"), message=msg)


@router.delete("/api/ui-executions/{execution_id}")
async def delete_execution_route(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_STOP)),
):
    """硬删除一次执行（含磁盘 artifact）。

    业务规则：
    * 必须是终态（completed / stopped / failed / aborted_budget）才能删除；
      非终态返回 409 提示用户先停止。
    * 删 DB ``ui_executions`` 行（cases / steps 走 FK ``ON DELETE CASCADE``）。
    * 同时 ``safe_unlink`` video / trace / 每个 step 的 screenshot——这些
      ``cleanup_old_media`` 是按时间扫的，删 DB 后扫不到 → 文件孤悬，必须
      在删除时同步清理。
    * 权限复用 ``UI_EXEC_STOP``：能停就能删（语义相邻），避免引入新权限/角色
      改动；但前端按钮文案明确警告"不可恢复"。
    """
    result = await delete_execution(db, execution_id, user)
    return success_response(data=result, message="执行记录已删除")


@router.post("/api/ui-executions/{execution_id}/retry-failed")
async def post_retry_failed(
    execution_id: uuid.UUID,
    body: ExecutionRetryRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_RUN)),
):
    """从原 execution 抽出失败/错误/跳过的用例，复用配置开新 execution。

    返回新 execution 的 list item；前端拿 id 去订阅 SSE 看进度。
    """
    item = await retry_failed_execution(
        db, execution_id, body or ExecutionRetryRequest(), user,
    )
    return success_response(
        data=item.model_dump(mode="json"),
        message="已派发重跑（仅失败用例）",
    )


# ─── Task 9.7：调试模式 + 历史回放 ────────────────────────────────────


@router.post("/api/ui-executions/{execution_id}/continue")
async def post_continue_execution(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_DEBUG)),
):
    """推进 debug 模式中卡在 step_paused 的 execution 到下一步。

    幂等：execution 不在 debug 暂停 / 已结束 → ``signal_delivered=False``，
    端点不报错。
    """
    result = await continue_debug_execution(db, execution_id, user)
    msg = (
        "已推进到下一步" if result.signal_delivered
        else "execution 不在调试暂停状态（可能已完成 / 已超时 / 不在 debug 模式）"
    )
    return success_response(data=result.model_dump(mode="json"), message=msg)


def _replay_streaming_response(
    execution_id: uuid.UUID,
    *,
    inter_step_delay_seconds: float,
    inter_case_delay_seconds: float,
) -> StreamingResponse:
    """SSE：历史事件回放。GET/POST 复用同一实现（前端 fetch SSE 默认 GET）。"""

    async def event_generator():
        async for chunk in replay_execution(
            execution_id,
            inter_step_delay_seconds=inter_step_delay_seconds,
            inter_case_delay_seconds=inter_case_delay_seconds,
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


@router.get("/api/ui-executions/{execution_id}/replay")
async def get_replay_execution(
    execution_id: uuid.UUID,
    inter_step_delay_seconds: float = Query(
        0.0, ge=0.0, le=10.0,
        description="每步事件之间的人工延时；0=瀑布即出，>0=按时间轴慢放",
    ),
    inter_case_delay_seconds: float = Query(
        0.0, ge=0.0, le=10.0,
        description="用例之间的人工延时",
    ),
    _user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """SSE 回放（GET）。与 ``POST .../replay`` 等价 —— ``useSSE`` 对无 body 请求走 GET。"""
    return _replay_streaming_response(
        execution_id,
        inter_step_delay_seconds=inter_step_delay_seconds,
        inter_case_delay_seconds=inter_case_delay_seconds,
    )


@router.post("/api/ui-executions/{execution_id}/replay")
async def post_replay_execution(
    execution_id: uuid.UUID,
    inter_step_delay_seconds: float = Query(
        0.0, ge=0.0, le=10.0,
        description="每步事件之间的人工延时；0=瀑布即出，>0=按时间轴慢放",
    ),
    inter_case_delay_seconds: float = Query(
        0.0, ge=0.0, le=10.0,
        description="用例之间的人工延时",
    ),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """SSE 接口：把 ``execution_id`` 对应的历史执行按事件序列重新吐出。

    不启动浏览器、不调 LLM —— 全部数据来自 ``ui_step_results`` 落库快照。
    前端复用 ``useExecutionSSE``，事件 payload 里带 ``replay=true`` 标记
    用以右上角徽标。
    """

    return _replay_streaming_response(
        execution_id,
        inter_step_delay_seconds=inter_step_delay_seconds,
        inter_case_delay_seconds=inter_case_delay_seconds,
    )


@router.get("/api/ui-executions/{execution_id}/video")
async def get_execution_video(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """流式返回 Playwright 录制的 webm/mp4 视频。

    路径校验：``video_path`` 来自后端写的绝对路径，但 FileResponse 会 follow
    symlink；为了防"路径穿越"，要求文件 ``isfile`` 且 size > 0。
    Engine 侧目前还没把 ``video_path`` 写入 DB（Task 9.5 留的空），实际拿到
    路径时返回 404 让前端按"暂无录制"展示。
    """
    row = await get_execution_or_404(db, execution_id, user)
    if not row.video_path or not os.path.isfile(row.video_path):
        raise NotFoundException("该执行没有录制视频或视频文件已被清理")
    return FileResponse(
        row.video_path,
        media_type="video/webm",
        filename=f"ui-exec-{execution_id}.webm",
    )


@router.get("/api/ui-executions/{execution_id}/trace")
async def get_execution_trace(
    execution_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """下载 Playwright trace.zip（含 dom snapshot / network / console）。

    用户拿到后可以本地用 ``npx playwright show-trace trace.zip`` 离线复现。
    """
    row = await get_execution_or_404(db, execution_id, user)
    if not row.trace_path or not os.path.isfile(row.trace_path):
        raise NotFoundException("该执行没有 trace 文件或文件已被清理")
    return FileResponse(
        row.trace_path,
        media_type="application/zip",
        filename=f"ui-exec-{execution_id}-trace.zip",
    )


_SCREENSHOT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@router.get("/api/ui-executions/steps/{step_id}/screenshot")
async def get_step_screenshot(
    step_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_EXEC_VIEW)),
):
    """Task 10.5：返回单步骤截图。

    路径校验同 video/trace 端点：``screenshot_path`` 是后端绝对路径，
    要求 ``isfile`` 且 size > 0；否则 404 让前端展示"暂无截图"。

    内容类型按扩展名兜底，未识别的扩展名一律按 ``image/png`` 返回——
    Playwright 默认输出 PNG，其它格式罕见。
    """
    step = await get_step_or_404(db, step_id, user)
    if not step.screenshot_path or not os.path.isfile(step.screenshot_path):
        raise NotFoundException("该步骤没有截图或截图文件已被清理")
    ext = os.path.splitext(step.screenshot_path)[1].lower()
    media_type = _SCREENSHOT_MIME.get(ext, "image/png")
    return FileResponse(step.screenshot_path, media_type=media_type)


# ─── 前置步骤试跑（Task 8.2）────────────────────────────────────────


@router.post(
    "/api/ui-environments/{env_id}/preconditions/{precondition_id}/test",
)
async def post_test_precondition(
    env_id: uuid.UUID,
    precondition_id: uuid.UUID,
    body: TestPreconditionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.UI_ENV_EDIT)),
):
    """试跑指定前置步骤（Task 8.2）。

    流程：启动一个临时 BrowserBundle → 跑 ``run_precondition`` → 关闭 → 返回
    详细结果（含截图、日志、state 状态、降级信息）。

    默认 ``persist_state=False``：试跑不污染正式 state 文件。如要在试跑同时
    更新 storage_state 文件，body 传 ``{"persist_state": true}``。

    响应里 ``success=False`` 不算端点错误（HTTP 200 也可能业务失败），前端
    应根据 ``error_kind`` 决定提示样式（``not_implemented`` = 等 Task 9.4 上线，
    ``browser_error`` = 浏览器环境未就绪等）。
    """
    body = body or TestPreconditionRequest()
    result = await test_precondition(
        db, env_id, precondition_id, user,
        persist_state=body.persist_state,
        timeout_seconds=body.timeout_seconds,
    )
    return success_response(data=result.model_dump(mode="json"))


# ─── 有头浏览器实时画面（noVNC 状态查询）─────────────────────────────


@router.get("/api/ui-automation/live-view/status")
async def get_live_view_status(
    user: User = Depends(get_current_user),
):
    """前端探测「实时画面」按钮是否要展示。

    返回三件事：
    1. ``enabled`` —— ``settings.UI_NOVNC_ENABLED`` 开关 + 容器内 websockify 端口
       是否真的在监听（避免镜像没装包但 settings 没改导致前端按钮点了 404）；
    2. ``proxy_path`` —— 给前端构造 iframe URL 用，统一是 ``/novnc/``，由
       frontend nginx 反代到 ``backend:UI_NOVNC_PORT``；
    3. ``hint`` —— 当 enabled=False 时给一句"为什么"，便于运维诊断。

    本端点只要求登录态（任意权限），不暴露任何敏感信息——画面流量本身经过
    nginx 反代，nginx 上 ``X-Frame-Options=SAMEORIGIN`` 限制 iframe 只能本站
    嵌入；外站直接拉 ws 流也拿不到鉴权 cookie，是 CSRF / 信息泄漏安全的。
    """
    import socket as _socket
    from app.config import settings as _settings

    enabled_flag = bool(_settings.UI_NOVNC_ENABLED)
    port = int(_settings.UI_NOVNC_PORT or 0)
    listening = False
    hint: str | None = None

    if not enabled_flag:
        hint = "settings.UI_NOVNC_ENABLED=false（部署时已显式关闭实时画面）"
    elif port <= 0:
        hint = "UI_NOVNC_PORT 未配置"
    else:
        # 做一次 1s 的 loopback 端口探活；websockify 启动失败 / Xvfb 没装时这里
        # 会失败，前端就不显示按钮了——比"按钮可点击但 iframe 转圈"友好。
        try:
            with _socket.create_connection(("127.0.0.1", port), timeout=1.0):
                listening = True
        except OSError as exc:
            hint = f"端口 {port} 未监听（websockify 未启动？）：{exc.__class__.__name__}"

    return success_response(data={
        "enabled": enabled_flag and listening,
        "proxy_path": "/novnc/",  # 前端拼 iframe URL：`${proxy_path}vnc_lite.html?path=${proxy_path}websockify&autoconnect=1&resize=remote`
        "port": port,
        "hint": hint,
    })
