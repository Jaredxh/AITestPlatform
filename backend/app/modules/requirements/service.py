import os
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.requirements.models import RequirementDocument
from app.modules.requirements.parser import MAX_FILE_SIZE, extract_text, validate_file
from app.modules.requirements.schemas import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentUpdateRequest,
    DocumentUploadResponse,
)


def _ensure_upload_dir() -> Path:
    path = Path(settings.REQUIREMENT_UPLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _summarize_review_state(doc: RequirementDocument) -> dict:
    reviews = list(getattr(doc, "reviews", []) or [])
    if not reviews:
        return {"review_status": "unreviewed", "review_count": 0, "last_review_score": None}

    total = len(reviews)
    latest = reviews[0]  # 已按 created_at desc 排序
    if latest.status == "completed":
        return {
            "review_status": "reviewed",
            "review_count": total,
            "last_review_score": latest.overall_score,
        }
    if latest.status == "failed":
        return {"review_status": "failed", "review_count": total, "last_review_score": None}
    return {"review_status": "reviewing", "review_count": total, "last_review_score": None}


def _to_list_response(doc: RequirementDocument) -> DocumentListResponse:
    review_state = _summarize_review_state(doc)
    return DocumentListResponse(
        id=doc.id,
        project_id=doc.project_id,
        filename=doc.filename,
        file_size=doc.file_size,
        content_type=doc.content_type,
        status=doc.status,
        uploaded_by=doc.uploaded_by,
        uploader_name=doc.uploader.display_name or doc.uploader.username if doc.uploader else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        **review_state,
    )


def _to_upload_response(doc: RequirementDocument) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        id=doc.id,
        project_id=doc.project_id,
        filename=doc.filename,
        file_size=doc.file_size,
        content_type=doc.content_type,
        status=doc.status,
        content_text=doc.content_text,
        uploaded_by=doc.uploaded_by,
        uploader_name=doc.uploader.display_name or doc.uploader.username if doc.uploader else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _to_detail_response(doc: RequirementDocument) -> DocumentDetailResponse:
    text = doc.content_text or ""
    review_state = _summarize_review_state(doc)
    return DocumentDetailResponse(
        id=doc.id,
        project_id=doc.project_id,
        filename=doc.filename,
        file_size=doc.file_size,
        content_type=doc.content_type,
        status=doc.status,
        content_text=doc.content_text,
        uploaded_by=doc.uploaded_by,
        uploader_name=doc.uploader.display_name or doc.uploader.username if doc.uploader else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        content_preview=text[:500] if text else None,
        **review_state,
    )


async def upload_document(
    db: AsyncSession,
    project_id: uuid.UUID,
    file: UploadFile,
    user: User,
) -> DocumentUploadResponse:
    """上传需求文档。

    解析容错策略：
    - 文件大小 / 类型校验失败 → 直接 422 拒绝
    - 文本抽取失败           → 仍然落库（status=parse_failed）+ 保存文件，
                              在响应中带上原始错误，前端可提示"自动解析失败，
                              已保存原始文件，可手动编辑或换 .docx 上传"，
                              比直接 422 让用户重试要友好得多（也避免被旧版本
                              .doc 文件挡在门外）。
    """
    import logging

    logger = logging.getLogger(__name__)

    content = await file.read()
    file_size = len(content)
    filename = file.filename or "unknown"
    content_type = file.content_type or "application/octet-stream"

    validate_file(content_type, file_size, filename)

    parse_error: str | None = None
    try:
        content_text = extract_text(content, content_type, filename)
        status = "parsed"
    except ValueError as e:
        logger.warning("Parse failed for %s (%s): %s", filename, content_type, e)
        content_text = None
        status = "parse_failed"
        parse_error = str(e)

    upload_dir = _ensure_upload_dir()
    stored_filename = f"{uuid.uuid4().hex}_{filename}"
    file_path = upload_dir / stored_filename

    with open(file_path, "wb") as f:
        f.write(content)

    doc = RequirementDocument(
        project_id=project_id,
        filename=filename,
        file_path=str(file_path),
        file_size=file_size,
        content_type=content_type,
        content_text=content_text,
        status=status,
        uploaded_by=user.id,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    response = _to_upload_response(doc)
    if parse_error:
        response.parse_error = parse_error
    return response


async def list_documents(
    db: AsyncSession,
    project_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> tuple[list[DocumentListResponse], int]:
    from sqlalchemy.orm import selectinload
    from app.modules.requirements.models import AIReview

    query = (
        select(RequirementDocument)
        .options(selectinload(RequirementDocument.reviews))
        .where(RequirementDocument.project_id == project_id)
    )
    count_query = (
        select(func.count())
        .select_from(RequirementDocument)
        .where(RequirementDocument.project_id == project_id)
    )

    if search:
        pattern = f"%{search}%"
        query = query.where(RequirementDocument.filename.ilike(pattern))
        count_query = count_query.where(RequirementDocument.filename.ilike(pattern))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.order_by(RequirementDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    docs = list(result.scalars().unique().all())

    # 临时引用以避免 lint 抱怨未使用导入
    _ = AIReview
    return [_to_list_response(d) for d in docs], total


async def get_document(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> DocumentDetailResponse:
    doc = await _get_doc_or_404(db, document_id)
    return _to_detail_response(doc)


async def delete_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    user: User,
) -> None:
    doc = await _get_doc_or_404(db, document_id)

    if doc.uploaded_by != user.id and not user.is_superuser:
        raise PermissionDeniedException("只有上传者或超管可以删除文档")

    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    await db.delete(doc)


async def update_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    data: DocumentUpdateRequest,
    user: User,
) -> DocumentDetailResponse:
    """允许编辑文档元数据：文件名、解析后的文本内容。

    用例：解析效果不理想或想手动补全片段时，由上传者 / 超管直接修订；评审重新发起后基于新文本运行。
    不会改写磁盘上的原始文件，只更新数据库中的 ``content_text``。
    """
    doc = await _get_doc_or_404(db, document_id)

    if doc.uploaded_by != user.id and not user.is_superuser:
        raise PermissionDeniedException("只有上传者或超管可以编辑文档")

    if data.filename is not None:
        doc.filename = data.filename.strip() or doc.filename
    if data.content_text is not None:
        doc.content_text = data.content_text
        # 文本被手工编辑后视为已成功解析
        doc.status = "parsed"

    await db.flush()
    await db.refresh(doc)
    return _to_detail_response(doc)


async def _get_doc_or_404(db: AsyncSession, document_id: uuid.UUID) -> RequirementDocument:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(RequirementDocument)
        .options(selectinload(RequirementDocument.reviews))
        .where(RequirementDocument.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundException("需求文档不存在")
    return doc
