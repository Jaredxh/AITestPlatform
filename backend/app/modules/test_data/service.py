"""测试物料模块的业务逻辑层。

职责：
- ``TestDataSet`` / ``TestDataItem`` 的 CRUD
- secret 类型的 Fernet 加解密
- file 类型的安全上传 + 下载路径解析 + 删除清理
- reveal 权限判定（owner / admin / test_data:reveal）+ 审计日志
- 物料集创建时按 scope 的业务规则强制（scope=environment 必须传 env_id；
  scope=personal 自动填 owner=当前用户；scope=project 拒绝 env_id/owner_id）

设计原则：
1. router 层只做 URL 路由 + 权限装饰器；所有业务校验 / 清理都在 service。
2. file 上传的扩展名黑名单 + MIME 正向列表双保险；单测可覆盖常见恶意文件。
3. 物料文件落盘位置：``<TEST_DATA_UPLOAD_DIR>/<project_id>/<set_id>/<uuid>_<sanitized_name>``。
   这样：
   - 按 project 分层，便于运维单独清项目；
   - uuid 前缀保证同名文件不覆盖；
   - 原文件名保留便于人肉诊断。
4. reveal 不通过 ORM event 自动触发——统一在 ``reveal_item`` 里调 ``logger.info``，
   用结构化格式方便后续接入审计日志表（Phase 11）。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Iterable

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.projects.models import Project, ProjectMember
from app.modules.test_data.models import (
    SCOPES,
    VALUE_TYPES,
    TestDataItem,
    TestDataSet,
)
from app.modules.test_data.schemas import (
    RecommendedSet,
    TestDataImportError,
    TestDataImportItem,
    TestDataImportReport,
    TestDataImportRequest,
    TestDataItemCreateRequest,
    TestDataItemResponse,
    TestDataItemRevealResponse,
    TestDataItemUpdateRequest,
    TestDataMergedItem,
    TestDataMergePreviewRequest,
    TestDataMergePreviewResponse,
    TestDataMergeSource,
    TestDataMissingAlert,
    TestDataMissingCheckRequest,
    TestDataMissingCheckResponse,
    TestDataMissingStepRef,
    TestDataSaveAsSetRequest,
    TestDataSetCloneRequest,
    TestDataSetCreateRequest,
    TestDataSetDetailResponse,
    TestDataSetResponse,
    TestDataSetUpdateRequest,
)

logger = logging.getLogger(__name__)


# ─── 文件类型策略 ─────────────────────────────────────────────────────

# 危险可执行扩展名：永远拒绝，即使 MIME 看起来正常
_BLOCKED_EXTENSIONS: frozenset[str] = frozenset({
    # Windows 可执行
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".dll", ".ps1",
    # Unix / macOS 可执行
    ".sh", ".bash", ".zsh", ".command", ".app", ".dmg", ".pkg",
    # 脚本 / 压缩炸弹常用
    ".jar", ".vbs", ".wsf",
})

# MIME 白名单：常见"测试上传"用途
_ALLOWED_MIME_PREFIXES: tuple[str, ...] = (
    "image/",      # png/jpg/gif/webp/svg+xml
    "text/",       # plain/csv/html/xml 等
    "application/pdf",
    "application/json",
    "application/xml",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-tar",
    "application/gzip",
    "application/vnd.openxmlformats-officedocument",  # .xlsx/.docx/.pptx
    "application/msword",                              # .doc
    "application/vnd.ms-excel",                        # .xls
    "application/vnd.ms-powerpoint",                   # .ppt
    "application/octet-stream",  # 兜底：很多 SDK 默认给这个
    "video/",
    "audio/",
)


_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def _sanitize_filename(raw: str) -> str:
    """把原始上传文件名转成安全的文件系统名。

    - 去路径分隔符（防 ``../xxx`` 注入）
    - 把非白名单字符替换成 ``_``（字母 / 数字 / 点 / 横线 / 下划线 / CJK）
    - 限长 100（防路径超 FS 限制）
    """
    name = os.path.basename(raw or "unknown").strip() or "unknown"
    name = _SAFE_FILENAME_CHARS.sub("_", name)
    if len(name) > 100:
        stem, _, ext = name.rpartition(".")
        if ext and len(ext) <= 10:
            name = stem[: 100 - len(ext) - 1] + "." + ext
        else:
            name = name[:100]
    return name or "unknown"


def _validate_file_upload(filename: str, content_type: str, size: int) -> None:
    """检查扩展名 / MIME / 大小；失败抛 ``AppException(422)``。"""
    if size <= 0:
        raise AppException("文件为空", code="EMPTY_FILE", status_code=422)
    if size > settings.TEST_DATA_MAX_FILE_SIZE:
        raise AppException(
            f"文件过大（{size} bytes），上限 {settings.TEST_DATA_MAX_FILE_SIZE} bytes",
            code="FILE_TOO_LARGE",
            status_code=422,
        )

    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _BLOCKED_EXTENSIONS:
        raise AppException(
            f"不允许上传 {ext} 扩展名的文件",
            code="BLOCKED_FILE_EXTENSION",
            status_code=422,
        )

    mime = (content_type or "").lower()
    if not mime:
        # 没给 MIME 直接兜底成 octet-stream；单扩展名检查已经足够
        return
    if not any(mime.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise AppException(
            f"不支持的文件 MIME 类型：{mime}",
            code="UNSUPPORTED_MIME",
            status_code=422,
        )


# ─── 权限 / 可见性 helpers ────────────────────────────────────────────


async def _ensure_project_member(
    db: AsyncSession, project_id: uuid.UUID, user: User,
) -> None:
    """非超管必须是项目成员；未加入的项目访问物料一律 404（不泄露存在性）。"""
    if user.is_superuser:
        return
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user.id,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise NotFoundException("项目不存在或无访问权限")


async def _ensure_project_exists(db: AsyncSession, project_id: uuid.UUID) -> None:
    res = await db.execute(select(Project.id).where(Project.id == project_id))
    if res.scalar_one_or_none() is None:
        raise NotFoundException("项目不存在")


def _ensure_user_can_see_set(ds: TestDataSet, user: User) -> None:
    """personal scope 的物料集只有 owner / 超管 / admin 可见。"""
    if user.is_superuser:
        return
    if ds.scope == "personal" and ds.owner_id != user.id:
        # 不透露"这个 id 存在但你看不到"，所以 raise NotFound
        raise NotFoundException("物料集不存在或无访问权限")


def _ensure_user_can_reveal(item: TestDataItem, ds: TestDataSet, user: User) -> None:
    """reveal 接口的额外权限门槛：

    - 超管 ✓
    - 拥有 ``test_data:reveal`` 权限 ✓
    - personal scope 且是 owner ✓
    - 其他一律 403
    """
    if user.is_superuser:
        return
    if user.has_permission(Permissions.TEST_DATA_REVEAL):
        return
    if ds.scope == "personal" and ds.owner_id == user.id:
        return
    raise PermissionDeniedException("无权查看该物料明文")


# ─── 转换 helpers ─────────────────────────────────────────────────────


def _to_set_response(ds: TestDataSet, *, item_count: int | None = None) -> TestDataSetResponse:
    return TestDataSetResponse(
        id=ds.id,
        project_id=ds.project_id,
        name=ds.name,
        description=ds.description,
        category=ds.category,
        scope=ds.scope,
        environment_id=ds.environment_id,
        owner_id=ds.owner_id,
        is_default=ds.is_default,
        created_by=ds.created_by,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
        item_count=item_count if item_count is not None else len(ds.items or []),
    )


def _to_set_detail(ds: TestDataSet) -> TestDataSetDetailResponse:
    items = [_to_item_response(it) for it in (ds.items or [])]
    return TestDataSetDetailResponse(
        **_to_set_response(ds, item_count=len(items)).model_dump(),
        items=items,
    )


def _to_item_response(it: TestDataItem) -> TestDataItemResponse:
    """转 response：secret 类型永远**不**暴露明文。"""
    is_secret = it.value_type == "secret"
    return TestDataItemResponse(
        id=it.id,
        set_id=it.set_id,
        key=it.key,
        value_type=it.value_type,
        description=it.description,
        sort_order=it.sort_order,
        value_text=None if is_secret else it.value_text,
        value_json=it.value_json,
        has_secret_value=bool(it.value_encrypted) if is_secret else False,
        file_path=it.file_path,
        file_size=it.file_size,
        file_mime=it.file_mime,
        created_at=it.created_at,
        updated_at=it.updated_at,
    )


# ─── TestDataSet CRUD ────────────────────────────────────────────────


async def list_sets(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    scope: str | None = None,
    environment_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TestDataSetResponse], int]:
    await _ensure_project_exists(db, project_id)
    await _ensure_project_member(db, project_id, user)

    base_q = select(TestDataSet).where(TestDataSet.project_id == project_id)

    if scope is not None:
        if scope not in SCOPES:
            raise AppException(f"scope 必须是 {SCOPES}", code="INVALID_SCOPE", status_code=400)
        base_q = base_q.where(TestDataSet.scope == scope)

    if environment_id is not None:
        base_q = base_q.where(TestDataSet.environment_id == environment_id)

    # personal scope：非超管只能看自己的
    if not user.is_superuser:
        # 用 or_(personal 且是我 / 非 personal)：personal 不是我就看不到
        from sqlalchemy import or_

        base_q = base_q.where(
            or_(
                TestDataSet.scope != "personal",
                TestDataSet.owner_id == user.id,
            ),
        )

    count_stmt = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    page_q = (
        base_q.order_by(TestDataSet.is_default.desc(), TestDataSet.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(page_q)).scalars().unique().all()

    # 用一次聚合把 item_count 拿到，避免 N+1
    if rows:
        count_q = (
            select(TestDataItem.set_id, func.count(TestDataItem.id))
            .where(TestDataItem.set_id.in_([r.id for r in rows]))
            .group_by(TestDataItem.set_id)
        )
        count_map = {sid: cnt for sid, cnt in (await db.execute(count_q)).all()}
    else:
        count_map = {}

    return [_to_set_response(ds, item_count=count_map.get(ds.id, 0)) for ds in rows], total


async def create_set(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: TestDataSetCreateRequest,
    user: User,
) -> TestDataSetDetailResponse:
    await _ensure_project_exists(db, project_id)
    await _ensure_project_member(db, project_id, user)

    # scope 专属的互斥校验
    if data.scope == "project":
        if data.environment_id is not None:
            raise AppException("scope=project 不允许指定 environment_id",
                               code="INVALID_SCOPE_FIELDS", status_code=422)
        owner_id = None
    elif data.scope == "environment":
        if data.environment_id is None:
            raise AppException("scope=environment 必须指定 environment_id",
                               code="MISSING_ENVIRONMENT_ID", status_code=422)
        # 轻量校验 env 属于同一个 project（避免跨项目注入）
        from app.modules.ui_automation.models import TestEnvironment
        env_row = (
            await db.execute(
                select(TestEnvironment.project_id).where(TestEnvironment.id == data.environment_id),
            )
        ).scalar_one_or_none()
        if env_row is None:
            raise NotFoundException("测试环境不存在")
        if env_row != project_id:
            raise AppException("测试环境不属于该项目",
                               code="ENV_PROJECT_MISMATCH", status_code=422)
        owner_id = None
    else:  # personal
        owner_id = user.id  # 忽略请求里的 owner_id，强制绑到当前用户

    ds = TestDataSet(
        project_id=project_id,
        name=data.name,
        description=data.description,
        category=data.category,
        scope=data.scope,
        environment_id=data.environment_id if data.scope == "environment" else None,
        owner_id=owner_id,
        is_default=data.is_default,
        created_by=user.id,
    )
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    return _to_set_detail(ds)


async def get_set_detail(
    db: AsyncSession, set_id: uuid.UUID, user: User,
) -> TestDataSetDetailResponse:
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)
    return _to_set_detail(ds)


async def update_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    data: TestDataSetUpdateRequest,
    user: User,
) -> TestDataSetDetailResponse:
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    payload = data.model_dump(exclude_unset=True)
    for field in ("name", "description", "category", "is_default"):
        if field in payload and payload[field] is not None:
            setattr(ds, field, payload[field])

    # 允许 description 清空（传 null）
    if "description" in payload and payload["description"] is None:
        ds.description = None

    await db.flush()
    await db.refresh(ds)
    return _to_set_detail(ds)


async def delete_set(db: AsyncSession, set_id: uuid.UUID, user: User) -> None:
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    # 先把所有 file 类型的物料文件 best-effort 删掉
    for it in (ds.items or []):
        if it.value_type == "file" and it.file_path:
            _best_effort_remove_file(it.file_path)

    await db.delete(ds)


# ─── TestDataItem CRUD ───────────────────────────────────────────────


async def list_items(
    db: AsyncSession, set_id: uuid.UUID, user: User,
) -> list[TestDataItemResponse]:
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)
    return [_to_item_response(it) for it in (ds.items or [])]


async def create_item(
    db: AsyncSession,
    set_id: uuid.UUID,
    data: TestDataItemCreateRequest,
    user: User,
) -> TestDataItemResponse:
    """创建**非 file** 类型物料；file 类型走 ``create_file_item``。"""
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    if data.value_type == "file":
        raise AppException(
            "file 类型物料必须通过 multipart 端点 /items/upload 上传",
            code="USE_FILE_UPLOAD_ENDPOINT",
            status_code=422,
        )

    await _ensure_key_unique(db, set_id, data.key)
    _validate_value_fields_by_type(data.value_type, data)

    item = TestDataItem(
        set_id=set_id,
        key=data.key,
        value_type=data.value_type,
        description=data.description,
        sort_order=data.sort_order,
        value_text=data.value_text if data.value_type in ("string", "multiline", "random") else None,
        value_encrypted=encrypt(data.value_secret) if data.value_type == "secret" and data.value_secret else None,
        value_json=data.value_json if data.value_type == "dataset" else None,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return _to_item_response(item)


async def create_file_item(
    db: AsyncSession,
    set_id: uuid.UUID,
    key: str,
    file: UploadFile,
    user: User,
    *,
    description: str | None = None,
    sort_order: int = 0,
) -> TestDataItemResponse:
    """创建 file 类型物料：校验 → 存盘 → 落库。

    失败时若文件已写盘会尝试回滚删除；DB 事务由 router 层的依赖管理。
    """
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    # key 格式 + 唯一性
    if not key:
        raise AppException("key 不能为空", code="INVALID_KEY", status_code=422)
    await _ensure_key_unique(db, set_id, key)

    content = await file.read()
    size = len(content)
    filename = file.filename or "unknown"
    mime = file.content_type or "application/octet-stream"
    _validate_file_upload(filename, mime, size)

    safe_name = _sanitize_filename(filename)
    project_dir = Path(settings.TEST_DATA_UPLOAD_DIR) / str(ds.project_id) / str(set_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    stored_path = project_dir / stored_name

    try:
        with open(stored_path, "wb") as fh:
            fh.write(content)
    except OSError as exc:
        raise AppException(f"写入文件失败：{exc}",
                           code="FILE_WRITE_FAILED", status_code=500) from exc

    # 存相对仓库根的路径，便于不同部署/挂载位置迁移；绝对路径依赖于部署目录
    try:
        rel_path = str(stored_path.relative_to(Path.cwd()))
    except ValueError:
        # cwd 之外的绝对路径，直接存
        rel_path = str(stored_path)

    item = TestDataItem(
        set_id=set_id,
        key=key,
        value_type="file",
        description=description,
        sort_order=sort_order,
        file_path=rel_path,
        file_size=size,
        file_mime=mime,
    )
    try:
        db.add(item)
        await db.flush()
        await db.refresh(item)
    except Exception:
        # 落库失败就把刚写的文件删掉，避免遗留垃圾
        _best_effort_remove_file(rel_path)
        raise

    return _to_item_response(item)


async def update_item(
    db: AsyncSession,
    item_id: uuid.UUID,
    data: TestDataItemUpdateRequest,
    user: User,
) -> TestDataItemResponse:
    item = await _get_item_or_404(db, item_id)
    ds = await _get_set_or_404(db, item.set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    payload = data.model_dump(exclude_unset=True)

    # 改 key 需要重新校验唯一性
    if "key" in payload and payload["key"] is not None and payload["key"] != item.key:
        await _ensure_key_unique(db, item.set_id, payload["key"])
        item.key = payload["key"]

    for field in ("description", "sort_order"):
        if field in payload and payload[field] is not None:
            setattr(item, field, payload[field])

    # 按 value_type 改对应字段（type 本身不允许改）
    if item.value_type in ("string", "multiline", "random"):
        if data.clear_value_text:
            item.value_text = None
        elif "value_text" in payload and payload["value_text"] is not None:
            item.value_text = payload["value_text"]

    if item.value_type == "secret":
        if data.clear_value_secret:
            item.value_encrypted = None
        elif "value_secret" in payload and payload["value_secret"] is not None:
            item.value_encrypted = encrypt(payload["value_secret"])

    if item.value_type == "dataset":
        if data.clear_value_json:
            item.value_json = None
        elif "value_json" in payload and payload["value_json"] is not None:
            item.value_json = payload["value_json"]

    await db.flush()
    await db.refresh(item)
    return _to_item_response(item)


async def delete_item(db: AsyncSession, item_id: uuid.UUID, user: User) -> None:
    item = await _get_item_or_404(db, item_id)
    ds = await _get_set_or_404(db, item.set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    if item.value_type == "file" and item.file_path:
        _best_effort_remove_file(item.file_path)

    await db.delete(item)


# ─── Reveal（secret 明文）────────────────────────────────────────────


async def reveal_item(
    db: AsyncSession, item_id: uuid.UUID, user: User,
) -> TestDataItemRevealResponse:
    item = await _get_item_or_404(db, item_id)
    ds = await _get_set_or_404(db, item.set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)
    _ensure_user_can_reveal(item, ds, user)

    secret_plain: str | None = None
    if item.value_type == "secret" and item.value_encrypted:
        try:
            secret_plain = decrypt(item.value_encrypted)
        except Exception as exc:  # noqa: BLE001
            raise AppException(
                f"凭据解密失败（item_id={item.id}）：请检查 ENCRYPT_KEY 是否一致",
                code="SECRET_DECRYPT_FAILED",
                status_code=500,
            ) from exc

    # 审计日志（结构化 + 不含明文）
    logger.info(
        "test_data.reveal user_id=%s username=%s item_id=%s set_id=%s key=%s value_type=%s",
        user.id, user.username, item.id, ds.id, item.key, item.value_type,
    )

    return TestDataItemRevealResponse(
        id=item.id,
        key=item.key,
        value_type=item.value_type,
        value_text=item.value_text if item.value_type != "secret" else None,
        value_secret=secret_plain,
        value_json=item.value_json,
    )


# ─── File 下载路径解析 ────────────────────────────────────────────────


async def resolve_file_item(
    db: AsyncSession, item_id: uuid.UUID, user: User,
) -> tuple[TestDataItem, Path]:
    """返回 ``(item, absolute_path)``；文件不在磁盘上时抛 404。"""
    item = await _get_item_or_404(db, item_id)
    ds = await _get_set_or_404(db, item.set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    if item.value_type != "file" or not item.file_path:
        raise AppException("该物料不是 file 类型", code="NOT_FILE_ITEM", status_code=422)

    abs_path = Path(item.file_path)
    if not abs_path.is_absolute():
        abs_path = Path.cwd() / item.file_path

    # 防路径逃逸：解析后必须仍在 upload 根目录下
    root = (Path.cwd() / settings.TEST_DATA_UPLOAD_DIR).resolve()
    try:
        resolved = abs_path.resolve()
    except OSError as exc:
        raise NotFoundException("物料文件不存在") from exc
    if not str(resolved).startswith(str(root)):
        # 可能是部署时改了 TEST_DATA_UPLOAD_DIR 后残留了旧路径；按 404 处理
        raise NotFoundException("物料文件不在受信目录内")

    if not resolved.exists():
        raise NotFoundException("物料文件不存在（可能已被删除或迁移）")

    return item, resolved


# ─── 内部 helpers ─────────────────────────────────────────────────────


async def _get_set_or_404(db: AsyncSession, set_id: uuid.UUID) -> TestDataSet:
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.id == set_id)
    )
    ds = (await db.execute(stmt)).scalar_one_or_none()
    if ds is None:
        raise NotFoundException("物料集不存在")
    return ds


async def _get_item_or_404(db: AsyncSession, item_id: uuid.UUID) -> TestDataItem:
    item = await db.get(TestDataItem, item_id)
    if item is None:
        raise NotFoundException("物料条目不存在")
    return item


async def _ensure_key_unique(db: AsyncSession, set_id: uuid.UUID, key: str) -> None:
    stmt = select(TestDataItem.id).where(
        TestDataItem.set_id == set_id,
        TestDataItem.key == key,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise AppException(
            f"key={key!r} 在本物料集中已存在",
            code="DUPLICATE_KEY",
            status_code=409,
        )


def _validate_value_fields_by_type(value_type: str, data: TestDataItemCreateRequest) -> None:
    """创建时强制按 type 填对应字段。

    允许"全空"的 item（比如占位，稍后再填）——除了 dataset 必须给 json，
    其他 type 都允许 value 暂时空着。
    """
    if value_type not in VALUE_TYPES:
        raise AppException(
            f"value_type 必须是 {VALUE_TYPES} 之一",
            code="INVALID_VALUE_TYPE",
            status_code=422,
        )
    if value_type == "dataset":
        # dataset 允许空值但推荐 list / dict
        if data.value_json is not None and not isinstance(data.value_json, (list, dict)):
            raise AppException(
                "dataset 类型的 value_json 必须是 list 或 dict",
                code="INVALID_DATASET_JSON",
                status_code=422,
            )


def _best_effort_remove_file(path: str) -> None:
    """删文件失败不 raise——避免"删数据库物料"因磁盘问题失败。"""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path.cwd() / path
        if p.exists():
            p.unlink()
    except Exception as exc:  # noqa: BLE001
        logger.warning("best_effort_remove_file(%s) 失败：%s", path, exc)


# ─── Task 8.6：批量导入 / 克隆 / 推荐 / save-as-set ─────────────────


# 可接受的 CSV 列名别名；全部转小写后匹配，支持"key/KEY/Key"。
_CSV_CANONICAL_COLUMNS = ("key", "value_type", "value", "description", "sort_order")
_CSV_COLUMN_ALIASES: dict[str, str] = {
    # canonical 列自身
    "key": "key",
    "value_type": "value_type",
    "value": "value",
    "description": "description",
    "sort_order": "sort_order",
    # 常见别名
    "名称": "key",
    "类型": "value_type",
    "值": "value",
    "说明": "description",
    "描述": "description",
    "排序": "sort_order",
    "order": "sort_order",
}


CSV_IMPORT_MAX_ROWS = 10_000


def parse_csv_to_items(csv_text: str) -> tuple[list[TestDataImportItem], list[TestDataImportError]]:
    """把 CSV 文本解析成 ``TestDataImportItem`` 列表 + 行级错误。

    - 必须有表头
    - 必须至少出现 ``key`` + ``value_type`` 两列
    - 不认识的列名会被忽略（不报错，留余地给用户加备注列）
    - 行级错误集中上报，不中断整个解析

    对不同 ``value_type`` 的 ``value`` 列解释：
    - string / multiline / random → 明文 → ``value_text``
    - secret → 明文 → ``value_secret`` （调用方继续走加密逻辑）
    - dataset → 尝试 ``json.loads``；失败则作为 string 原样写入 value_json（会被
      后续校验拒绝）
    - file → CSV 中不允许；会产生行错误
    """
    if not csv_text:
        raise AppException("CSV 内容为空", code="EMPTY_CSV", status_code=422)

    # 去 UTF-8 BOM
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise AppException("CSV 缺少表头", code="CSV_MISSING_HEADER", status_code=422)

    # 规范化列名
    normalized_fields = {}
    for raw in reader.fieldnames:
        if raw is None:
            continue
        key = raw.strip().lower()
        canonical = _CSV_COLUMN_ALIASES.get(key)
        if canonical is not None:
            normalized_fields[raw] = canonical

    values = set(normalized_fields.values())
    missing_required = {"key", "value_type"} - values
    if missing_required:
        raise AppException(
            f"CSV 缺少必需列：{sorted(missing_required)}（支持别名：见文档）",
            code="CSV_MISSING_REQUIRED_COLUMNS",
            status_code=422,
        )

    items: list[TestDataImportItem] = []
    errors: list[TestDataImportError] = []

    for row_no, raw_row in enumerate(reader, start=1):
        if row_no > CSV_IMPORT_MAX_ROWS:
            errors.append(TestDataImportError(
                row=row_no,
                message=f"超过单次导入上限 {CSV_IMPORT_MAX_ROWS} 行，后续行已忽略",
            ))
            break

        row: dict[str, str] = {}
        for raw_key, canonical in normalized_fields.items():
            v = raw_row.get(raw_key)
            row[canonical] = v.strip() if isinstance(v, str) else ""

        # 整行空白（Excel 尾部经常带空行）→ 静默跳过不算错
        if not any(row.get(c) for c in _CSV_CANONICAL_COLUMNS):
            continue

        key = row.get("key", "")
        value_type = row.get("value_type", "")
        raw_value = row.get("value", "")
        description = row.get("description") or None
        sort_order_raw = row.get("sort_order", "")

        if not key:
            errors.append(TestDataImportError(row=row_no, key=None, message="缺少 key"))
            continue
        if not value_type:
            errors.append(TestDataImportError(row=row_no, key=key, message="缺少 value_type"))
            continue
        if value_type not in VALUE_TYPES:
            errors.append(TestDataImportError(
                row=row_no, key=key,
                message=f"value_type 非法：{value_type}，必须是 {list(VALUE_TYPES)} 之一",
            ))
            continue
        if value_type == "file":
            errors.append(TestDataImportError(
                row=row_no, key=key,
                message="file 类型不支持通过 CSV 导入，请用 multipart 上传端点",
            ))
            continue

        try:
            sort_order = int(sort_order_raw) if sort_order_raw else 0
        except ValueError:
            errors.append(TestDataImportError(
                row=row_no, key=key,
                message=f"sort_order 必须是整数：{sort_order_raw!r}",
            ))
            continue

        # 按 type 填入对应字段
        value_text: str | None = None
        value_secret: str | None = None
        value_json = None

        if value_type in ("string", "multiline", "random"):
            value_text = raw_value or None
        elif value_type == "secret":
            value_secret = raw_value or None
        elif value_type == "dataset":
            if raw_value:
                try:
                    value_json = json.loads(raw_value)
                except json.JSONDecodeError as exc:
                    errors.append(TestDataImportError(
                        row=row_no, key=key,
                        message=f"dataset 值不是合法 JSON：{exc.msg}",
                    ))
                    continue

        try:
            items.append(TestDataImportItem(
                key=key,
                value_type=value_type,
                description=description,
                sort_order=sort_order,
                value_text=value_text,
                value_secret=value_secret,
                value_json=value_json,
            ))
        except Exception as exc:  # noqa: BLE001 - Pydantic 校验
            errors.append(TestDataImportError(row=row_no, key=key, message=str(exc)))

    return items, errors


async def import_items(
    db: AsyncSession,
    set_id: uuid.UUID,
    user: User,
    items: list[TestDataImportItem],
    *,
    mode: str = "skip_existing",
    starting_errors: list[TestDataImportError] | None = None,
) -> TestDataImportReport:
    """把一批 import items 写进指定 set。

    冲突策略：
    - ``skip_existing``：已存在同 key → 跳过（计入 ``skipped``）
    - ``upsert``：已存在 → 按导入条目的 value_type 更新对应 value 字段；
      ``value_type`` 与现有不一致时归为行错误（不做静默类型切换）

    返回的 ``report`` 包含 created / updated / skipped / errors 明细。
    ``starting_errors`` 用于把"CSV 解析阶段产生的错误"一并透传到报告里，
    避免前端要合并两个结果。
    """
    ds = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, ds.project_id, user)
    _ensure_user_can_see_set(ds, user)

    report = TestDataImportReport(
        created=0, updated=0, skipped=0,
        errors=list(starting_errors or []),
        total=len(items),
    )

    if not items:
        return report

    # 预取现有 items 放到 map，省得每条 import 再查一次数据库
    existing = {it.key: it for it in (ds.items or [])}

    for idx, imp in enumerate(items, start=1):
        if imp.value_type == "file":
            report.errors.append(TestDataImportError(
                row=idx, key=imp.key,
                message="file 类型不支持通过批量导入，请用 multipart 上传",
            ))
            continue

        try:
            if imp.key in existing:
                if mode == "skip_existing":
                    report.skipped += 1
                    continue
                # upsert：必须同 value_type 才覆盖
                target = existing[imp.key]
                if target.value_type != imp.value_type:
                    report.errors.append(TestDataImportError(
                        row=idx, key=imp.key,
                        message=f"既有 value_type={target.value_type!r}，与导入 {imp.value_type!r} 不一致；"
                                "改类型请先删除原条目",
                    ))
                    continue

                _apply_import_value_to_item(target, imp)
                if imp.description is not None:
                    target.description = imp.description
                if imp.sort_order is not None:
                    target.sort_order = imp.sort_order
                report.updated += 1
            else:
                new_item = TestDataItem(
                    set_id=set_id,
                    key=imp.key,
                    value_type=imp.value_type,
                    description=imp.description,
                    sort_order=imp.sort_order,
                )
                _apply_import_value_to_item(new_item, imp)
                db.add(new_item)
                # 即时写到 session 保证下一条同 key 能被 existing 识别（先 flush 再更新 map）
                existing[imp.key] = new_item
                report.created += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("import_items row=%d key=%r 失败", idx, imp.key)
            report.errors.append(TestDataImportError(row=idx, key=imp.key, message=str(exc)))

    await db.flush()
    return report


def _apply_import_value_to_item(item: TestDataItem, imp: TestDataImportItem) -> None:
    """把 ``TestDataImportItem`` 的 value 字段写到 ``TestDataItem`` 对应列。

    - string / multiline / random → value_text
    - secret → value_encrypted（Fernet 加密）
    - dataset → value_json
    其他 type 的残留字段会被显式清空，避免"从 secret 改回 string 但 encrypted
    还在"的脏数据。
    """
    vt = imp.value_type
    # 先清空所有 value 字段
    item.value_text = None
    item.value_encrypted = None
    item.value_json = None
    if vt in ("string", "multiline", "random"):
        item.value_text = imp.value_text
    elif vt == "secret":
        if imp.value_secret:
            item.value_encrypted = encrypt(imp.value_secret)
    elif vt == "dataset":
        if imp.value_json is not None and not isinstance(imp.value_json, (list, dict)):
            raise AppException(
                "dataset 类型的 value_json 必须是 list 或 dict",
                code="INVALID_DATASET_JSON",
                status_code=422,
            )
        item.value_json = imp.value_json
    # file 在 CSV/JSON 导入路径已经被拦截，此处不会走到


async def import_csv_to_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    user: User,
    file: UploadFile,
    *,
    mode: str = "skip_existing",
) -> TestDataImportReport:
    """上传 CSV → 解析 → 批量导入（一体化，router 层只管拿 file）。

    - 文件大小硬限制 10MB（CSV 正常不可能这么大；防滥用）
    - 编码假定 UTF-8（含 BOM），非 UTF-8 会在 decode 时抛 400
    """
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise AppException("CSV 文件过大（>10MB）", code="CSV_TOO_LARGE", status_code=422)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppException(
            f"CSV 必须 UTF-8 编码：{exc}",
            code="CSV_ENCODING", status_code=422,
        ) from exc

    items, parse_errors = parse_csv_to_items(text)
    return await import_items(db, set_id, user, items, mode=mode, starting_errors=parse_errors)


async def import_json_to_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    user: User,
    request: TestDataImportRequest,
) -> TestDataImportReport:
    return await import_items(
        db, set_id, user, request.items, mode=request.mode,
    )


async def clone_set(
    db: AsyncSession,
    set_id: uuid.UUID,
    user: User,
    data: TestDataSetCloneRequest,
) -> TestDataSetDetailResponse:
    """克隆一个物料集。

    行为：
    1. 创建新 set（``created_by=当前用户``，``scope`` 由 request 或继承）
    2. 把源 set 所有 items 复制；file 类型走物理 ``shutil.copy`` 到新 set 目录
    3. secret 的 ``value_encrypted`` 保留原值（直接 re-use 密文，无需重新解密再加密）

    安全：
    - 源 set 必须可见（_ensure_user_can_see_set）
    - 如果请求 ``scope=project / environment``，需要当前用户是项目成员（由
      外层 `_ensure_project_member` 覆盖）
    """
    src = await _get_set_or_404(db, set_id)
    await _ensure_project_member(db, src.project_id, user)
    _ensure_user_can_see_set(src, user)

    # 决定目标 scope
    target_scope = data.scope or src.scope
    if target_scope not in SCOPES:
        raise AppException(f"scope 必须是 {SCOPES}", code="INVALID_SCOPE", status_code=400)

    # 非 owner 克隆 personal 集合：强制改成目标用户的 personal 副本
    # （否则他看不到 owner != 自己的 personal set，语义很怪）
    if src.scope == "personal" and src.owner_id != user.id and not user.is_superuser:
        target_scope = "personal"

    target_env_id: uuid.UUID | None = None
    target_owner_id: uuid.UUID | None = None

    if target_scope == "environment":
        target_env_id = data.environment_id or src.environment_id
        if target_env_id is None:
            raise AppException("clone 到 environment scope 必须指定 environment_id",
                               code="MISSING_ENVIRONMENT_ID", status_code=422)
        # 校验 env 属于同一 project
        from app.modules.ui_automation.models import TestEnvironment
        env_row = (await db.execute(
            select(TestEnvironment.project_id).where(TestEnvironment.id == target_env_id),
        )).scalar_one_or_none()
        if env_row is None:
            raise NotFoundException("目标测试环境不存在")
        if env_row != src.project_id:
            raise AppException("目标测试环境不属于源项目",
                               code="ENV_PROJECT_MISMATCH", status_code=422)
    elif target_scope == "personal":
        target_owner_id = user.id

    new_set = TestDataSet(
        project_id=src.project_id,
        name=data.new_name,
        description=data.description if data.description is not None else src.description,
        category=data.category if data.category is not None else src.category,
        scope=target_scope,
        environment_id=target_env_id,
        owner_id=target_owner_id,
        is_default=data.is_default,
        created_by=user.id,
    )
    db.add(new_set)
    await db.flush()  # 取到 new_set.id

    # 复制 items
    for src_item in (src.items or []):
        new_item = TestDataItem(
            set_id=new_set.id,
            key=src_item.key,
            value_type=src_item.value_type,
            description=src_item.description,
            sort_order=src_item.sort_order,
            value_text=src_item.value_text,
            value_encrypted=src_item.value_encrypted,  # 密文直接复用，不解密
            value_json=src_item.value_json,
        )
        if src_item.value_type == "file" and src_item.file_path:
            # 物理拷贝文件到新 set 目录
            new_path = _clone_file_to_new_set(src, new_set, src_item)
            new_item.file_path = new_path
            new_item.file_size = src_item.file_size
            new_item.file_mime = src_item.file_mime
        db.add(new_item)

    await db.flush()
    await db.refresh(new_set)
    return _to_set_detail(new_set)


def _clone_file_to_new_set(
    src_set: TestDataSet, new_set: TestDataSet, src_item: TestDataItem,
) -> str:
    """把源 item 的物理文件拷贝到新 set 目录，返回新 item 的 file_path（相对）。

    拷贝失败（源文件丢失 / 权限问题）→ raise AppException，整个 clone 事务
    会回滚（不留半截新 set）。
    """
    src_path = Path(src_item.file_path) if src_item.file_path else None
    if src_path is None:
        raise AppException(f"源物料 {src_item.key} 缺少 file_path",
                           code="SOURCE_FILE_MISSING", status_code=500)
    if not src_path.is_absolute():
        src_path = Path.cwd() / src_path
    if not src_path.exists():
        raise AppException(f"源物料 {src_item.key} 的文件已丢失：{src_path}",
                           code="SOURCE_FILE_NOT_FOUND", status_code=422)

    new_dir = Path(settings.TEST_DATA_UPLOAD_DIR) / str(new_set.project_id) / str(new_set.id)
    new_dir.mkdir(parents=True, exist_ok=True)
    # 保留原文件名后缀 + 同前缀风格（uuid + _ + original）
    stored_name = f"{uuid.uuid4().hex}_{src_path.name.split('_', 1)[-1] if '_' in src_path.name else src_path.name}"
    new_path = new_dir / stored_name
    try:
        shutil.copy2(src_path, new_path)
    except OSError as exc:
        raise AppException(f"复制物料文件失败：{exc}",
                           code="FILE_COPY_FAILED", status_code=500) from exc

    try:
        return str(new_path.relative_to(Path.cwd()))
    except ValueError:
        return str(new_path)


async def save_overrides_as_set(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    data: TestDataSaveAsSetRequest,
) -> TestDataSetDetailResponse:
    """把弹窗里的 ``overrides`` 落地为新 set + items。

    本质是 ``create_set`` + ``import_items(upsert)`` 的组合，但对外语义更具体，
    避免前端要调两次 API。默认 scope=personal（"我临时改的数据"通常是私人偏好）。
    """
    await _ensure_project_exists(db, project_id)
    await _ensure_project_member(db, project_id, user)

    # scope 约束与 create_set 一致
    target_env_id: uuid.UUID | None = None
    target_owner_id: uuid.UUID | None = None
    if data.scope == "project":
        if data.environment_id is not None:
            raise AppException("scope=project 不允许指定 environment_id",
                               code="INVALID_SCOPE_FIELDS", status_code=422)
    elif data.scope == "environment":
        if data.environment_id is None:
            raise AppException("scope=environment 必须指定 environment_id",
                               code="MISSING_ENVIRONMENT_ID", status_code=422)
        target_env_id = data.environment_id
        # 验证 env 属于同一项目
        from app.modules.ui_automation.models import TestEnvironment
        env_row = (await db.execute(
            select(TestEnvironment.project_id).where(TestEnvironment.id == data.environment_id),
        )).scalar_one_or_none()
        if env_row is None:
            raise NotFoundException("测试环境不存在")
        if env_row != project_id:
            raise AppException("测试环境不属于该项目",
                               code="ENV_PROJECT_MISMATCH", status_code=422)
    else:  # personal
        target_owner_id = user.id

    new_set = TestDataSet(
        project_id=project_id,
        name=data.name,
        description=data.description,
        category=data.category,
        scope=data.scope,
        environment_id=target_env_id,
        owner_id=target_owner_id,
        is_default=False,
        created_by=user.id,
    )
    db.add(new_set)
    await db.flush()

    # 批量写 items，复用 import_items 的逻辑（走 upsert；新 set 没冲突）
    report = await import_items(
        db, new_set.id, user, data.overrides, mode="upsert",
    )
    if report.errors:
        # 任何一条 override 出错都视为整体失败，回滚
        messages = "；".join(f"[row {e.row}:{e.key}] {e.message}" for e in report.errors[:5])
        raise AppException(
            f"save-as-set 部分条目失败：{messages}",
            code="SAVE_AS_SET_PARTIAL_FAILURE",
            status_code=422,
        )

    await db.refresh(new_set)
    return _to_set_detail(new_set)


async def recommend_sets(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    testcase_ids: list[uuid.UUID] | None = None,
    environment_id: uuid.UUID | None = None,
    top_n: int = 10,
) -> list[RecommendedSet]:
    """给前端的"推荐加载"物料集列表。

    策略（验收反馈调整后，2026-05）：
    1. 当前环境的 ``default_data_set_ids`` → "环境默认"（``env_default``）
    2. 项目级 ``is_default=True`` 的 set → "项目默认"
    3. 指定的 testcase 的 ``default_data_set_ids`` → "用例默认"
    4. 当前用户的 personal set → "我的物料"
    5. 该项目内 item 数最多的 top3（作为"最常用"启发值）→ "常用集合"

    按顺序合并、去重（set.id），最多返回 ``top_n`` 项。前端会把
    ``project_default`` / ``testcase_default`` / ``env_default`` 三类自动勾选，
    用户仍可再次取消（实现「环境选了默认集后弹窗下方默认勾上」）。
    """
    await _ensure_project_exists(db, project_id)
    await _ensure_project_member(db, project_id, user)

    seen: dict[uuid.UUID, RecommendedSet] = {}

    def _push(ds: TestDataSet, *, reason: str, reason_code: str) -> None:
        if ds.id in seen:
            return
        if ds.scope == "personal" and ds.owner_id != user.id and not user.is_superuser:
            return  # 别人的私人物料不能推荐给我
        seen[ds.id] = RecommendedSet(
            set=_to_set_response(ds, item_count=len(ds.items or [])),
            reason=reason,
            reason_code=reason_code,
        )

    # 0) 环境默认（最优先，UI 上让用户能直观看到当前环境会自动加载哪些集）
    if environment_id is not None:
        from app.modules.ui_automation.models import TestEnvironment

        env_row = (
            await db.execute(
                select(TestEnvironment).where(TestEnvironment.id == environment_id),
            )
        ).scalar_one_or_none()
        env_default_ids: list[uuid.UUID] = []
        if env_row is not None:
            for sid in env_row.default_data_set_ids or []:
                try:
                    env_default_ids.append(uuid.UUID(str(sid)))
                except (ValueError, TypeError):
                    continue
        if env_default_ids:
            stmt_env_default = (
                select(TestDataSet)
                .options(selectinload(TestDataSet.items))
                .where(TestDataSet.id.in_(env_default_ids))
            )
            order_map = {sid: i for i, sid in enumerate(env_default_ids)}
            ordered_env_default_sets = sorted(
                (await db.execute(stmt_env_default)).scalars().unique().all(),
                key=lambda ds: order_map.get(ds.id, 10**9),
            )
            for ds in ordered_env_default_sets:
                _push(ds, reason="环境默认加载", reason_code="env_default")

    # 1) 项目默认
    stmt_default = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.project_id == project_id, TestDataSet.is_default.is_(True))
        .order_by(TestDataSet.created_at.asc())
    )
    for ds in (await db.execute(stmt_default)).scalars().unique().all():
        _push(ds, reason="项目默认加载", reason_code="project_default")

    # 2) 用例默认
    if testcase_ids:
        from app.modules.testcases.models import Testcase
        tc_rows = (await db.execute(
            select(Testcase.default_data_set_ids).where(
                Testcase.id.in_(testcase_ids),
                Testcase.project_id == project_id,
            ),
        )).all()
        tc_set_ids: set[uuid.UUID] = set()
        for (id_list,) in tc_rows:
            if not id_list:
                continue
            for sid in id_list:
                try:
                    tc_set_ids.add(uuid.UUID(str(sid)))
                except (ValueError, TypeError):
                    continue

        if tc_set_ids:
            stmt_tc = (
                select(TestDataSet)
                .options(selectinload(TestDataSet.items))
                .where(TestDataSet.id.in_(tc_set_ids))
            )
            for ds in (await db.execute(stmt_tc)).scalars().unique().all():
                _push(ds, reason="用例指定的默认物料", reason_code="testcase_default")

    # 3) personal scope（自己的）
    stmt_personal = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(
            TestDataSet.project_id == project_id,
            TestDataSet.scope == "personal",
            TestDataSet.owner_id == user.id,
        )
        .order_by(TestDataSet.updated_at.desc())
    )
    for ds in (await db.execute(stmt_personal)).scalars().unique().all():
        _push(ds, reason="我的个人物料", reason_code="personal")

    # 4) 按 item 数量 top3（启发值，作为"常用"的兜底）
    # 此处做子查询计数：避免 Python 侧 O(N) 拿全部 set
    item_count_sub = (
        select(TestDataItem.set_id, func.count(TestDataItem.id).label("cnt"))
        .group_by(TestDataItem.set_id)
        .subquery()
    )
    stmt_popular = (
        select(TestDataSet, item_count_sub.c.cnt)
        .options(selectinload(TestDataSet.items))
        .join(item_count_sub, item_count_sub.c.set_id == TestDataSet.id)
        .where(TestDataSet.project_id == project_id)
        .where(
            or_(
                TestDataSet.scope != "personal",
                TestDataSet.owner_id == user.id,
            ),
        )
        .order_by(item_count_sub.c.cnt.desc())
        .limit(3)
    )
    for ds, _cnt in (await db.execute(stmt_popular)).unique().all():
        _push(ds, reason="常用物料集", reason_code="popular")

    return list(seen.values())[:top_n]


# ─── Task 9.3：preview-merge / missing-check ────────────────────────


def serialize_resolver_for_preview(
    resolver,
    *,
    sources_by_key: dict[str, list[TestDataMergeSource]] | None = None,
    configured_set_ids: list[uuid.UUID] | None = None,
) -> list[TestDataMergedItem]:
    """把已合并的 resolver.data 序列化为弹窗预览行（不暴露 secret 明文）。

    传入的是任意拥有 ``data: dict[key, TestDataItem]`` 的对象，便于单测
    无 DB 也可以直测这层（不必 import resolver 模块本身）。

    ``sources_by_key`` 可选，由 ``_collect_preview_candidates`` 提供。每个
    key 对应一个"候选来源"列表（按合并顺序追加，最后一条 = 胜出值），
    用于让前端展示同名 key 在多个物料集中的所有候选——之前合并预览只显
    示最终生效值，多集同名场景下用户看不到被覆盖的候选（验收反馈）。

    ``configured_set_ids`` 给定时，**只保留**源集合在该列表里的条目，外
    加：AI 自造 / 临时覆盖 / 无源 adhoc 项（这些都不属于任何物料集）。
    实现验收反馈"弹窗下方只展示我选的物料集的明细，不展示项目里全部物料"。
    None 时回退到旧行为（导出全部合并物料）。
    """
    sources_by_key = sources_by_key or {}
    allowed: set[uuid.UUID] | None = (
        {uuid.UUID(str(sid)) for sid in configured_set_ids}
        if configured_set_ids is not None
        else None
    )
    rows: list[TestDataMergedItem] = []
    for key in sorted(resolver.data.keys()):
        item = resolver.data[key]
        if allowed is not None:
            src_id = getattr(item, "source_set_id", None)
            # 只过滤"明确来自某物料集且不在 configured 列表"的条目；adhoc /
            # synthetic / manual override（src_id 为 None）永远保留。
            if src_id is not None and src_id not in allowed:
                continue
        rows.append(
            TestDataMergedItem(
                key=key,
                value_type=item.value_type,
                description=item.description,
                display_value=item.display_safe_value(),
                has_secret_value=(item.value_type == "secret" and bool(item.value_encrypted)),
                file_name=(
                    Path(item.file_path).name if item.value_type == "file" and item.file_path else None
                ),
                synthetic_source=item.synthetic_source,
                sources=sources_by_key.get(key, []),
            ),
        )
    return rows


async def _collect_preview_candidates(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    request: TestDataMergePreviewRequest,
) -> dict[str, list[TestDataMergeSource]]:
    """按合并层级遍历所有相关物料集，**为每个 key 累积全部候选来源**。

    这是 ``preview_merge`` 用来回答"同 key 在多个集合里都有，到底哪个胜出、
    哪些被覆盖"的核心函数。语义对齐 ``TestDataResolver.build`` 的层级顺序：

        personal → project → environment_bind → environment_default_sets
        → loaded（弹窗勾选 + 用例 default）→ manual_overrides

    数组里**最后一条 = 胜出值**；其它条 ``overridden=True``。用单独函数+独立
    DB 查询而不复用 resolver，是为了避免改 resolver 内部接口（resolver 当前
    只输出 flat items 列表，不带 set 元信息），这里牺牲一次额外读 DB 换取
    零侵入。preview 不是热路径，性能可接受。

    去重规则（验收反馈）：
        以 set_id 为 dedup 键。同一个物料集即使同时是「项目 scope」「环境
        默认集」「弹窗勾选集」也只生成一条来源——使用其**自身的 scope**
        （personal/project/environment/testcase），不再把"加载层"当 scope
        汇报。这样用户配置同一物料集后只看到一条来源，不再误以为有多条。
    """
    from app.modules.test_data.models import TestDataSet
    from app.modules.testcases.models import Testcase
    from app.modules.ui_automation.models import TestEnvironment

    candidates: dict[str, list[TestDataMergeSource]] = {}
    pushed_set_ids: set[uuid.UUID] = set()

    def _push_set(ds) -> None:
        """把一个 ORM ``TestDataSet`` 的 items 推入 candidates；按 set_id 去重。

        scope 用 ``ds.scope`` 自身的 personal/project/environment 等，不再
        反映该集是"如何被纳入"的（避免同一物料集出现多条来源）。
        """
        if ds.id in pushed_set_ids:
            return
        pushed_set_ids.add(ds.id)
        items = list(getattr(ds, "items", None) or [])
        items.sort(key=lambda r: (r.sort_order, r.key))
        scope = getattr(ds, "scope", "project") or "project"
        for row in items:
            src = TestDataMergeSource(
                set_id=ds.id,
                set_name=ds.name,
                scope=scope,
                display_value=_display_for_item_row(row),
                has_secret_value=(row.value_type == "secret" and bool(row.value_encrypted)),
                file_name=(
                    Path(row.file_path).name
                    if row.value_type == "file" and row.file_path
                    else None
                ),
                overridden=False,
            )
            candidates.setdefault(row.key, []).append(src)

    # ─ 1. personal scope ────────────────────────────────────────────
    personal_q = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(
            TestDataSet.project_id == project_id,
            TestDataSet.scope == "personal",
            TestDataSet.owner_id == user.id,
        )
        .order_by(TestDataSet.created_at.asc())
    )
    for ds in (await db.execute(personal_q)).scalars().unique().all():
        _push_set(ds)

    # ─ 2. project scope ─────────────────────────────────────────────
    project_q = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(
            TestDataSet.project_id == project_id,
            TestDataSet.scope == "project",
        )
        .order_by(TestDataSet.created_at.asc())
    )
    for ds in (await db.execute(project_q)).scalars().unique().all():
        _push_set(ds)

    # ─ 3. environment binding + 环境默认集 ──────────────────────────
    env_default_ids: list[uuid.UUID] = []
    if request.environment_id is not None:
        env_q = (
            select(TestDataSet)
            .options(selectinload(TestDataSet.items))
            .where(
                TestDataSet.scope == "environment",
                TestDataSet.environment_id == request.environment_id,
            )
            .order_by(TestDataSet.created_at.asc())
        )
        for ds in (await db.execute(env_q)).scalars().unique().all():
            _push_set(ds)

        env_row = (
            await db.execute(
                select(TestEnvironment).where(TestEnvironment.id == request.environment_id),
            )
        ).scalar_one_or_none()
        if env_row is not None:
            for sid in env_row.default_data_set_ids or []:
                try:
                    env_default_ids.append(uuid.UUID(str(sid)))
                except (ValueError, TypeError):
                    continue

    if env_default_ids:
        for ds in await _ordered_sets_with_items_for_preview(db, env_default_ids):
            _push_set(ds)

    # ─ 4. loaded（弹窗勾选）+ 用例默认集 ────────────────────────────
    loaded_ids: list[uuid.UUID] = list(request.set_ids)
    testcase_default_ids: list[uuid.UUID] = []
    if request.testcase_ids:
        rows = (
            await db.execute(
                select(Testcase.default_data_set_ids).where(
                    Testcase.id.in_(request.testcase_ids),
                    Testcase.project_id == project_id,
                ),
            )
        ).all()
        seen: set[uuid.UUID] = set()
        for (id_list,) in rows:
            for sid in id_list or []:
                try:
                    parsed = uuid.UUID(str(sid))
                except (ValueError, TypeError):
                    continue
                if parsed in seen:
                    continue
                seen.add(parsed)
                testcase_default_ids.append(parsed)

    # 与 ``_build_preview_resolver`` 同步：testcase_default 拼到 loaded 之后
    final_loaded: list[uuid.UUID] = []
    for sid in loaded_ids:
        if sid not in final_loaded:
            final_loaded.append(sid)
    for sid in testcase_default_ids:
        if sid not in final_loaded:
            final_loaded.append(sid)

    if final_loaded:
        for ds in await _ordered_sets_with_items_for_preview(db, final_loaded):
            _push_set(ds)

    # ─ 5. manual override（最后一层、最高优先级）────────────────────
    for k in (request.manual_overrides or {}).keys():
        candidates.setdefault(k, []).append(
            TestDataMergeSource(
                set_id=None,
                set_name="（弹窗手动改写）",
                scope="manual",
                display_value="●●●●" if any(
                    s.has_secret_value for s in candidates.get(k, [])
                ) else "（已临时改写）",
                has_secret_value=any(
                    s.has_secret_value for s in candidates.get(k, [])
                ),
                file_name=None,
                overridden=False,
            ),
        )

    # ─ 6. 标记 overridden：每个 key 的最后一条胜出，其它都被覆盖 ───
    for sources in candidates.values():
        for src in sources[:-1]:
            src.overridden = True

    return candidates


def _display_for_item_row(row) -> str:
    """``TestDataItem`` ORM row → 安全展示值（与 ``TestDataItem.display_safe_value`` 对齐，
    但这里直接拿 ORM row 算，不绕一圈 dataclass）。"""
    if row.value_type == "secret":
        return "●●●●" if row.value_encrypted else ""
    if row.value_type == "file":
        if row.file_path:
            return Path(row.file_path).name
        return ""
    if row.value_type == "multiline":
        text = row.value_text or ""
        return text if len(text) <= 80 else text[:77] + "..."
    if row.value_type == "random":
        return "（随机，执行时生成）"
    if row.value_type == "dataset":
        try:
            n = len(row.value_json or [])
        except TypeError:
            n = 0
        return f"（数据组，{n} 项）"
    return row.value_text or ""


async def _ordered_sets_with_items_for_preview(
    db: AsyncSession, set_ids: list[uuid.UUID],
):
    """复刻 resolver 的 ``_ordered_sets_with_items``，但放在 preview 里使用——
    避免引入对 ui_automation 包的依赖（preview 是 test_data 自己的功能）。"""
    from app.modules.test_data.models import TestDataSet

    if not set_ids:
        return []
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.id.in_(set_ids))
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    order_map = {sid: i for i, sid in enumerate(set_ids)}
    return sorted(rows, key=lambda ds: order_map.get(ds.id, 10**9))


async def _build_preview_resolver(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    request: TestDataMergePreviewRequest,
):
    """构造预览用 resolver。

    复用 ``TestDataResolver.build``：通过最小的 ExecutionLike-stub 把
    project / triggered_by / environment 传进去；用例的
    ``default_data_set_ids`` 在预览阶段近似为额外的 loaded 集（与执行时
    的"用例级覆盖"语义略有不同，但足够给前端"实际会加载哪些 set"看）。
    """
    from dataclasses import dataclass

    from app.modules.testcases.models import Testcase
    from app.modules.ui_automation.test_data_resolver import TestDataResolver

    await _ensure_project_exists(db, project_id)
    await _ensure_project_member(db, project_id, user)

    @dataclass
    class _PreviewExec:
        triggered_by: uuid.UUID
        project_id: uuid.UUID
        environment_id: uuid.UUID | None

    extra_loaded: list[uuid.UUID] = []
    if request.testcase_ids:
        rows = (
            await db.execute(
                select(Testcase.default_data_set_ids).where(
                    Testcase.id.in_(request.testcase_ids),
                    Testcase.project_id == project_id,
                ),
            )
        ).all()
        seen: set[uuid.UUID] = set()
        for (id_list,) in rows:
            for sid in id_list or []:
                try:
                    parsed = uuid.UUID(str(sid))
                except (ValueError, TypeError):
                    continue
                if parsed in seen:
                    continue
                seen.add(parsed)
                extra_loaded.append(parsed)

    set_ids: list[uuid.UUID] = list(request.set_ids)
    for sid in extra_loaded:
        if sid not in set_ids:
            set_ids.append(sid)

    return await TestDataResolver.build(
        db,
        _PreviewExec(
            triggered_by=user.id,
            project_id=project_id,
            environment_id=request.environment_id,
        ),
        manual_overrides=request.manual_overrides,
        loaded_set_ids=set_ids,
    )


async def preview_merge(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    request: TestDataMergePreviewRequest,
) -> TestDataMergePreviewResponse:
    """执行配置弹窗：把"勾选的 sets + 临时覆盖"合并预览（secret 永不暴露）。

    每条 ``MergedItem`` 还会带 ``sources`` —— 该 key 在合并链中出现过的
    全部来源（带 set_name / scope / 是否被覆盖），让前端能展示同名 key 在多
    个集合中的所有候选值，方便用户判断是否要调整加载顺序。

    2026-05 验收反馈调整：返回的 items 已经按"本次配置的物料集"过滤过——
    用户在弹窗下方只看到自己勾选 / 环境默认 / 用例默认的明细，不再被项目
    个人 scope 物料污染。AI 自造 / 临时覆盖 / adhoc 不受过滤影响。
    """
    resolver = await _build_preview_resolver(db, project_id, user, request)
    sources_by_key = await _collect_preview_candidates(db, project_id, user, request)
    configured_set_ids = await _collect_configured_set_ids_for_preview(
        db, project_id, request,
    )
    items = serialize_resolver_for_preview(
        resolver,
        sources_by_key=sources_by_key,
        configured_set_ids=configured_set_ids,
    )
    return TestDataMergePreviewResponse(items=items)


async def _collect_configured_set_ids_for_preview(
    db: AsyncSession,
    project_id: uuid.UUID,
    request: TestDataMergePreviewRequest,
) -> list[uuid.UUID]:
    """汇总「弹窗里本次显式配置」的物料集 id（保持顺序、去重）。

    与 ``ui_automation.execution_engine._collect_configured_set_ids`` 同语义，
    但放在 test_data 里复用预览逻辑：
    1. ``request.set_ids``——弹窗勾选的物料集
    2. 环境的 ``default_data_set_ids``——切环境后默认勾上的集合
    3. 用例的 ``default_data_set_ids``——用例层默认绑定的集合
    """
    from app.modules.testcases.models import Testcase
    from app.modules.ui_automation.models import TestEnvironment

    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []

    def _push(sid: Any) -> None:
        try:
            parsed = uuid.UUID(str(sid))
        except (ValueError, TypeError):
            return
        if parsed in seen:
            return
        seen.add(parsed)
        out.append(parsed)

    for sid in request.set_ids or []:
        _push(sid)

    if request.environment_id is not None:
        env_row = (
            await db.execute(
                select(TestEnvironment).where(TestEnvironment.id == request.environment_id),
            )
        ).scalar_one_or_none()
        if env_row is not None:
            for sid in env_row.default_data_set_ids or []:
                _push(sid)

    if request.testcase_ids:
        rows = (
            await db.execute(
                select(Testcase.default_data_set_ids).where(
                    Testcase.id.in_(list(request.testcase_ids)),
                    Testcase.project_id == project_id,
                ),
            )
        ).all()
        for (id_list,) in rows:
            for sid in id_list or []:
                _push(sid)

    return out


async def missing_check(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    request: TestDataMissingCheckRequest,
) -> TestDataMissingCheckResponse:
    """缺料预检（非阻断）：把一组用例的 ``{{key}}`` 与 resolver 合并键集求差。"""
    from sqlalchemy.orm import selectinload

    from app.modules.testcases.models import Testcase
    from app.modules.ui_automation.preflight import preflight_data_check

    resolver = await _build_preview_resolver(db, project_id, user, request)

    if not request.testcase_ids:
        return TestDataMissingCheckResponse(
            missing_keys=[],
            will_synthesize=True,
            details=[],
        )

    stmt = (
        select(Testcase)
        .options(selectinload(Testcase.steps))
        .where(
            Testcase.id.in_(request.testcase_ids),
            Testcase.project_id == project_id,
        )
    )
    testcases = list((await db.execute(stmt)).scalars().unique().all())

    alerts = await preflight_data_check(testcases, resolver)
    details = [
        TestDataMissingAlert(
            key=a.key,
            will_synthesize=a.will_synthesize,
            detected_in_steps=[
                TestDataMissingStepRef(
                    testcase_id=uuid.UUID(s.testcase_id),
                    step_number=s.step_number,
                    where=s.where,
                )
                for s in a.detected_in_steps
            ],
        )
        for a in alerts
    ]
    return TestDataMissingCheckResponse(
        missing_keys=[a.key for a in alerts],
        will_synthesize=True,
        details=details,
    )


__all__ = [
    "create_file_item",
    "create_item",
    "create_set",
    "delete_item",
    "delete_set",
    "get_set_detail",
    "list_items",
    "list_sets",
    "reveal_item",
    "resolve_file_item",
    "update_item",
    "update_set",
    # Task 8.6
    "clone_set",
    "import_csv_to_set",
    "import_items",
    "import_json_to_set",
    "parse_csv_to_items",
    "recommend_sets",
    "save_overrides_as_set",
    # Task 9.3
    "missing_check",
    "preview_merge",
    "serialize_resolver_for_preview",
]


# 仅为了在单测和其他模块里引用
def iter_supported_value_types() -> Iterable[str]:
    return VALUE_TYPES
