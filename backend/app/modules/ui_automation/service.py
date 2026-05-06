"""UI 自动化模块的业务逻辑层。

职责：
- TestEnvironment / PreconditionTemplate 的 CRUD
- 创建环境时**自动从 base_url 提取 hostname** 写入 allowed_hosts（用户没填时）
- 前置步骤的 ``credentials`` dict ⇄ ``credentials_encrypted`` 字符串的双向加解密
- ``clear_state`` 操作：调 ``state_manager.mark_state_stale`` + 清 DB 字段

权限校验：本层不直接判权限（router 层用 ``require_permission`` 装饰器统一卡），
只做"项目成员可见性"的最小校验：环境必须属于用户能访问的 project，
否则返回 404 而不是 403（符合"不暴露资源是否存在"的安全惯例）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import decrypt, encrypt
from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.projects.models import Project, ProjectMember
from app.modules.ui_automation import state_manager
from app.modules.ui_automation.models import (
    PRECONDITION_TYPES,
    PreconditionTemplate,
    TestEnvironment,
)
from app.modules.ui_automation.precondition_executor import (
    PreconditionResult,
    run_precondition,
)
from app.modules.ui_automation.schemas import (
    ClearStateResponse,
    PreconditionTemplateCreateRequest,
    PreconditionTemplateResponse,
    PreconditionTemplateUpdateRequest,
    TestEnvironmentCreateRequest,
    TestEnvironmentDetailResponse,
    TestEnvironmentResponse,
    TestEnvironmentUpdateRequest,
    TestPreconditionResponse,
)

# ─── 转换 helpers ─────────────────────────────────────────────────────


def _to_env_response(env: TestEnvironment) -> TestEnvironmentResponse:
    return TestEnvironmentResponse(
        id=env.id,
        project_id=env.project_id,
        name=env.name,
        description=env.description,
        base_url=env.base_url,
        allowed_hosts=list(env.allowed_hosts or []),
        token_budget=env.token_budget,
        enable_browser_evaluate=env.enable_browser_evaluate,
        session_name=env.session_name,
        state_saved_at=env.state_saved_at,
        default_data_set_ids=[str(x) for x in (env.default_data_set_ids or [])],
        headless=env.headless,
        viewport_width=env.viewport_width,
        viewport_height=env.viewport_height,
        created_at=env.created_at,
        updated_at=env.updated_at,
    )


def _to_precondition_response(pt: PreconditionTemplate) -> PreconditionTemplateResponse:
    return PreconditionTemplateResponse(
        id=pt.id,
        environment_id=pt.environment_id,
        name=pt.name,
        type=pt.type,
        description=pt.description,
        config=dict(pt.config or {}),
        has_credentials=bool(pt.credentials_encrypted),
        order_index=pt.order_index,
        enabled=pt.enabled,
        state_saved_at=pt.state_saved_at,
        created_at=pt.created_at,
        updated_at=pt.updated_at,
    )


def _to_env_detail(env: TestEnvironment) -> TestEnvironmentDetailResponse:
    base = _to_env_response(env)
    return TestEnvironmentDetailResponse(
        **base.model_dump(),
        preconditions=[_to_precondition_response(p) for p in env.preconditions],
    )


# ─── 工具：base_url → host ────────────────────────────────────────────


def extract_host_from_url(url: str) -> str | None:
    """从 URL 中提取 hostname（含端口，如果非默认端口）。

    示例：
    - ``https://staging.foo.com/login`` → ``staging.foo.com``
    - ``http://localhost:8443`` → ``localhost:8443``
    - ``https://staging.foo.com:443`` → ``staging.foo.com``（443 是 https 默认端口，省略）
    - ``http://staging.foo.com:80`` → ``staging.foo.com``（80 是 http 默认端口）

    返回 None 表示 URL 无法解析（service 层据此决定是否报错）。
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return None
    if not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    port = parsed.port
    if port is not None:
        scheme_default = {"http": 80, "https": 443}.get(parsed.scheme.lower())
        if port != scheme_default:
            host = f"{host}:{port}"
    return host


# ─── 权限 / 可见性 ────────────────────────────────────────────────────


async def _check_project_member(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
    """非超管必须是项目成员才能 R / W 项目下的 UI 资源。"""
    if user.is_superuser:
        return
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        # 用 NotFound 而不是 PermissionDenied，避免泄漏"项目存在与否"
        raise NotFoundException("项目不存在或无访问权限")


async def _ensure_project_exists(db: AsyncSession, project_id: uuid.UUID) -> None:
    result = await db.execute(select(Project.id).where(Project.id == project_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundException("项目不存在")


# ─── TestEnvironment CRUD ────────────────────────────────────────────


async def list_environments(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TestEnvironmentResponse], int]:
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    query = (
        select(TestEnvironment)
        .where(TestEnvironment.project_id == project_id)
        .order_by(TestEnvironment.created_at.desc())
    )
    count_stmt = (
        select(func.count())
        .select_from(TestEnvironment)
        .where(TestEnvironment.project_id == project_id)
    )
    total = (await db.execute(count_stmt)).scalar() or 0
    page_query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(page_query)).scalars().unique().all()
    return [_to_env_response(env) for env in rows], total


async def create_environment(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: TestEnvironmentCreateRequest,
    user: User,
) -> TestEnvironmentDetailResponse:
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    base_url_str = str(data.base_url)
    allowed_hosts = list(data.allowed_hosts or [])
    if not allowed_hosts:
        host = extract_host_from_url(base_url_str)
        if not host:
            raise AppException("base_url 无法解析出 host，无法初始化 allowed_hosts",
                               code="INVALID_BASE_URL", status_code=400)
        allowed_hosts = [host]

    env = TestEnvironment(
        project_id=project_id,
        name=data.name,
        description=data.description,
        base_url=base_url_str,
        allowed_hosts=allowed_hosts,
        token_budget=data.token_budget,
        enable_browser_evaluate=data.enable_browser_evaluate,
        session_name=data.session_name,
        default_data_set_ids=[str(x) for x in (data.default_data_set_ids or [])],
        headless=data.headless,
        viewport_width=data.viewport_width,
        viewport_height=data.viewport_height,
    )
    db.add(env)
    await db.flush()
    await db.refresh(env)
    return _to_env_detail(env)


async def get_environment_detail(
    db: AsyncSession, env_id: uuid.UUID, user: User,
) -> TestEnvironmentDetailResponse:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)
    return _to_env_detail(env)


async def update_environment(
    db: AsyncSession,
    env_id: uuid.UUID,
    data: TestEnvironmentUpdateRequest,
    user: User,
) -> TestEnvironmentDetailResponse:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)

    payload = data.model_dump(exclude_unset=True)
    for field in ("name", "description", "token_budget", "enable_browser_evaluate",
                  "session_name", "headless", "viewport_width", "viewport_height"):
        if field in payload and payload[field] is not None:
            setattr(env, field, payload[field])

    if "base_url" in payload and payload["base_url"] is not None:
        env.base_url = str(payload["base_url"])

    if "allowed_hosts" in payload and payload["allowed_hosts"] is not None:
        env.allowed_hosts = list(payload["allowed_hosts"])

    if "default_data_set_ids" in payload and payload["default_data_set_ids"] is not None:
        env.default_data_set_ids = [str(x) for x in payload["default_data_set_ids"]]

    await db.flush()
    await db.refresh(env)
    return _to_env_detail(env)


async def delete_environment(db: AsyncSession, env_id: uuid.UUID, user: User) -> None:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)
    # CASCADE 在 DB 层会带走所有 PreconditionTemplate；同时清掉 state 文件，
    # 避免遗留垃圾。state 文件失败不阻塞删除（用户已经表达"不要这个环境了"）。
    try:
        await state_manager.mark_state_stale(
            env.id, session_name=env.session_name, db_clear_callback=None,
        )
    except Exception:  # noqa: BLE001
        pass
    await db.delete(env)


async def clear_environment_state(
    db: AsyncSession, env_id: uuid.UUID, user: User,
) -> ClearStateResponse:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)

    async def _clear_db_field() -> None:
        env.state_saved_at = None
        await db.flush()

    file_existed, file_removed = await state_manager.mark_state_stale(
        env.id,
        session_name=env.session_name,
        db_clear_callback=_clear_db_field,
    )
    return ClearStateResponse(
        environment_id=env.id,
        state_file_existed=file_existed,
        state_file_removed=file_removed,
    )


# ─── PreconditionTemplate CRUD ────────────────────────────────────────


async def list_preconditions(
    db: AsyncSession, env_id: uuid.UUID, user: User,
) -> list[PreconditionTemplateResponse]:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)
    return [_to_precondition_response(p) for p in env.preconditions]


async def create_precondition(
    db: AsyncSession,
    env_id: uuid.UUID,
    data: PreconditionTemplateCreateRequest,
    user: User,
) -> PreconditionTemplateResponse:
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)
    _ensure_type_valid(data.type)

    encrypted = _encrypt_credentials(data.credentials)
    pt = PreconditionTemplate(
        environment_id=env.id,
        name=data.name,
        type=data.type,
        description=data.description,
        config=dict(data.config or {}),
        credentials_encrypted=encrypted,
        order_index=data.order_index,
        enabled=data.enabled,
    )
    db.add(pt)
    await db.flush()
    await db.refresh(pt)
    return _to_precondition_response(pt)


async def update_precondition(
    db: AsyncSession,
    precondition_id: uuid.UUID,
    data: PreconditionTemplateUpdateRequest,
    user: User,
) -> PreconditionTemplateResponse:
    pt = await _get_precondition_or_404(db, precondition_id)
    env = await _get_env_or_404(db, pt.environment_id)
    await _check_project_member(db, env.project_id, user)

    payload = data.model_dump(exclude_unset=True)
    if "type" in payload and payload["type"] is not None:
        _ensure_type_valid(payload["type"])

    for field in ("name", "type", "description", "order_index", "enabled"):
        if field in payload and payload[field] is not None:
            setattr(pt, field, payload[field])

    if "config" in payload and payload["config"] is not None:
        pt.config = dict(payload["config"])

    # credentials 三态：
    #   - clear_credentials=True → 清空（优先级最高）
    #   - credentials 为 None → 不改
    #   - credentials 为 dict → 覆盖加密
    if data.clear_credentials:
        pt.credentials_encrypted = None
    elif data.credentials is not None:
        pt.credentials_encrypted = _encrypt_credentials(data.credentials)

    await db.flush()
    await db.refresh(pt)
    return _to_precondition_response(pt)


async def delete_precondition(
    db: AsyncSession, precondition_id: uuid.UUID, user: User,
) -> None:
    pt = await _get_precondition_or_404(db, precondition_id)
    env = await _get_env_or_404(db, pt.environment_id)
    await _check_project_member(db, env.project_id, user)
    await db.delete(pt)


# ─── Task 8.2 - 试跑前置步骤（test-precondition 端点）─────────────────


async def test_precondition(
    db: AsyncSession,
    env_id: uuid.UUID,
    precondition_id: uuid.UUID,
    user: User,
    *,
    persist_state: bool = False,
    timeout_seconds: float = 300.0,
) -> TestPreconditionResponse:
    """启动一个临时 BrowserBundle，跑指定前置步骤，返回结果。

    安全 / 健壮性约束：
    1. precondition 必须归属指定 env（路径双校验，防 IDOR）
    2. 用户必须是该项目成员
    3. BrowserBundle 启动失败（Chromium 没装 / 端口被占）→ 返回明确 503-like
       错误而不是 raise；前端能直接展示
    4. ``persist_state=False`` 时 executor 内部跳过 storage_state 写盘
    5. State 持久化的回调里同步更新 environment / precondition 的
       ``state_saved_at``（**仅** persist_state=True 才生效）

    设计取舍：本函数 fully self-contained — 自启浏览器、自跑、自关，不返
    回 BrowserBundle 给上层。原因：试跑场景极短（≤60s），不需要复用 Bundle；
    完整 execution 时由 Task 9.4 ExecutionEngine 自管 Bundle 生命周期。
    """
    # 双重存在性 + 归属校验
    pt = await _get_precondition_or_404(db, precondition_id)
    if pt.environment_id != env_id:
        raise NotFoundException("前置步骤不属于该环境")
    env = await _get_env_or_404(db, env_id)
    await _check_project_member(db, env.project_id, user)

    # 解密凭据（仅本调用栈持有，不写日志）
    credentials = reveal_credentials(pt) if pt.credentials_encrypted else None

    state_target = state_manager.state_path_for(env.id, session_name=env.session_name)

    # State 写盘后的 DB 同步
    async def _on_state_saved(_path: Path) -> None:
        if not persist_state:
            return
        env.state_saved_at = datetime.now(timezone.utc)
        pt.state_saved_at = env.state_saved_at
        await db.flush()

    async def _on_state_invalidated() -> None:
        env.state_saved_at = None
        await db.flush()

    # 启动 BrowserBundle —— 失败时把 PreconditionResult 手工拼成"启动失败"
    # 而不是让端点 500，对前端更友好。
    try:
        from app.modules.ui_automation.browser_bundle import (  # 局部导入避免顶层装载 playwright
            BrowserBundle,
            BundleOptions,
        )
    except ImportError as exc:
        raise AppException(
            f"BrowserBundle 模块不可用：{exc}",
            code="BROWSER_BUNDLE_UNAVAILABLE",
            status_code=503,
        ) from exc

    bundle_options = BundleOptions(
        headless=env.headless,
        # 让 BrowserBundle 在 open 时**就**载入 storage_state（如果 state_inject 模板
        # 想跑 "navigate + 检测过期" 流程，context 必须已加载 state）。
        storage_state_path=(
            str(state_target) if state_target.exists() else None
        ),
        mcp_enabled=True,  # 试跑时 MCP 可用更佳，不可用也能 fallback
    )

    bundle = None
    try:
        bundle = await BrowserBundle.open(env, uuid.uuid4(), options=bundle_options)
    except Exception as exc:  # noqa: BLE001
        # 典型：Chromium 没装 / 端口耗尽 / playwright 未安装
        # 直接拼一个失败 result 返回，让前端看到"环境未就绪"
        result = PreconditionResult(
            template_id=pt.id,
            template_name=pt.name,
            type=pt.type,
            success=False,
            elapsed_ms=0,
            error=f"BrowserBundle 启动失败：{exc}",
            error_kind="browser_error",
            logs=[
                f"{type(exc).__name__}: {exc}",
                "提示：开发环境若未装 Chromium，请先 `uv run playwright install chromium`",
                "或参考 docs/PHASE2_DEPLOYMENT_NOTES.md 完成 Task 11.3 部署",
            ],
        )
        return _result_to_response(result)

    # 仅 ai_login（含 state_inject 过期降级）需要构造真正的 LLM 驱动 runner；
    # 另外三种类型完全不会触碰 runner，所以"没 LLM 配置"也不影响它们。
    ai_login_runner = None
    if pt.type in ("ai_login", "state_inject"):
        ai_login_runner = await _build_ai_login_runner_for_test(db, env)

    try:
        result = await run_precondition(
            bundle, pt,
            base_url=env.base_url,
            state_target=state_target if persist_state else None,
            credentials=credentials,
            on_state_saved=_on_state_saved if persist_state else None,
            on_state_invalidated=_on_state_invalidated if persist_state else None,
            ai_login_runner=ai_login_runner,
            capture_screenshot=True,
            save_state_on_success=persist_state,
            per_template_timeout_seconds=timeout_seconds,
        )
    finally:
        try:
            await bundle.close()
        except Exception:  # noqa: BLE001
            # 关浏览器失败不应该影响"试跑结果"返回
            pass

    return _result_to_response(result)


async def _build_ai_login_runner_for_test(
    db: AsyncSession, env: TestEnvironment,
) -> Any:
    """试跑端点专用：加载默认 LLM 配置 → 包成 ``StepRunnerAILoginRunner``。

    返回 ``None`` 时 caller 会让 ``run_precondition`` 退化到 stub，stub 给
    出"未配置 LLM"提示。这样的设计避免了"试跑 ai_login 但 LLM 未配置"
    时把整个端点搞成 500，而是让前端能稳定拿到 ``error_kind=not_implemented``
    的结构化结果，引导用户先去配置 LLM。
    """
    from sqlalchemy import asc

    from app.modules.llm.models import LLMConfig
    from app.modules.ui_automation.ai_login_runner import build_ai_login_runner

    # 跟 execution_engine._load_llm_config 保持一致：
    # 1. is_default=True 的配置；2. 兜底库里第一条
    default_row = (
        await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    llm_orm = default_row or (
        await db.execute(
            select(LLMConfig).order_by(asc(LLMConfig.created_at)).limit(1)
        )
    ).scalar_one_or_none()

    return build_ai_login_runner(
        llm_config_orm=llm_orm,
        environment=env,
        budget_limit=env.token_budget,
    )


def _result_to_response(result: PreconditionResult) -> TestPreconditionResponse:
    """``PreconditionResult`` dataclass → ``TestPreconditionResponse`` schema。

    避免 service 把 dataclass 直接吐给 router，保持"router 看到的全是 schema"
    的洁癖（也方便未来给 response 加版本字段）。
    """
    return TestPreconditionResponse(
        template_id=result.template_id,
        template_name=result.template_name,
        type=result.type,
        success=result.success,
        elapsed_ms=result.elapsed_ms,
        error=result.error,
        error_kind=result.error_kind,
        screenshot_base64=result.screenshot_base64,
        state_was_loaded=result.state_was_loaded,
        state_was_stale=result.state_was_stale,
        state_was_saved=result.state_was_saved,
        state_saved_path=result.state_saved_path,
        fell_back_to=result.fell_back_to,
        logs=list(result.logs),
    )


# ─── 凭据加解密（仅 service 内部使用）────────────────────────────────


def _encrypt_credentials(credentials: dict | None) -> str | None:
    """dict → Fernet 加密字符串。

    None / 空 dict 都返回 None（视为"无凭据"）；非空 dict 才真正加密。
    json.dumps + ensure_ascii=False 让中文 / emoji 等字符不被转义浪费长度。
    """
    if not credentials:
        return None
    plaintext = json.dumps(credentials, ensure_ascii=False, separators=(",", ":"))
    return encrypt(plaintext)


def reveal_credentials(pt: PreconditionTemplate) -> dict | None:
    """解密读出凭据明文。**仅在执行器内部使用**，绝不通过 router 暴露。

    Task 8.2 ``PreconditionRunner`` 拿到 PreconditionTemplate 后调本函数；
    本 task 不通过 API 暴露任何 reveal 端点。
    """
    if not pt.credentials_encrypted:
        return None
    try:
        plaintext = decrypt(pt.credentials_encrypted)
        data = json.loads(plaintext)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        # 解密失败可能是 ENCRYPT_KEY 在不同环境间不同步导致的；提示运维。
        raise AppException(
            f"凭据解密失败（precondition_id={pt.id}），请检查 ENCRYPT_KEY 是否一致",
            code="CREDENTIALS_DECRYPT_FAILED",
            status_code=500,
        ) from exc


# ─── 内部 helpers ─────────────────────────────────────────────────────


def _ensure_type_valid(t: str) -> None:
    if t not in PRECONDITION_TYPES:
        raise AppException(
            f"前置步骤 type 必须是 {PRECONDITION_TYPES} 之一",
            code="INVALID_PRECONDITION_TYPE", status_code=400,
        )


async def _get_env_or_404(db: AsyncSession, env_id: uuid.UUID) -> TestEnvironment:
    stmt = (
        select(TestEnvironment)
        .options(selectinload(TestEnvironment.preconditions))
        .where(TestEnvironment.id == env_id)
    )
    env = (await db.execute(stmt)).scalar_one_or_none()
    if env is None:
        raise NotFoundException("测试环境不存在")
    return env


async def _get_precondition_or_404(
    db: AsyncSession, pt_id: uuid.UUID,
) -> PreconditionTemplate:
    pt = await db.get(PreconditionTemplate, pt_id)
    if pt is None:
        raise NotFoundException("前置步骤不存在")
    return pt


# Re-export PermissionDeniedException 让上层 catch 时无需额外 import
__all__ = [
    "extract_host_from_url",
    "list_environments",
    "create_environment",
    "get_environment_detail",
    "update_environment",
    "delete_environment",
    "clear_environment_state",
    "list_preconditions",
    "create_precondition",
    "update_precondition",
    "delete_precondition",
    "reveal_credentials",
    "test_precondition",
    "PermissionDeniedException",
]
