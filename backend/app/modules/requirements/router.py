import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.requirements.review_service import (
    delete_review,
    get_review,
    list_reviews,
    trigger_review,
)
from app.modules.requirements.schemas import DocumentUpdateRequest, ReviewTriggerRequest
from app.modules.requirements.service import (
    delete_document,
    get_document,
    list_documents,
    update_document,
    upload_document,
)

router = APIRouter(prefix="/api/requirements", tags=["需求文档管理"])


@router.post("/projects/{project_id}/documents", response_model=dict)
async def upload(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_UPLOAD)),
):
    """上传需求文档（支持 .docx / .pdf），自动解析文本内容。"""
    doc = await upload_document(db, project_id, file, current_user)
    return success_response(data=doc.model_dump(mode="json"), message="文档上传成功")


@router.get("/projects/{project_id}/documents", response_model=dict)
async def list_docs(
    project_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_VIEW)),
):
    """获取项目下的需求文档列表。"""
    docs, total = await list_documents(db, project_id, page, page_size, search)
    return success_response(
        data={
            "items": [d.model_dump(mode="json") for d in docs],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/documents/{document_id}", response_model=dict)
async def get_detail(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_VIEW)),
):
    """获取需求文档详情，包括解析后的全文内容。"""
    doc = await get_document(db, document_id)
    return success_response(data=doc.model_dump(mode="json"))


@router.put("/documents/{document_id}", response_model=dict)
async def update(
    document_id: uuid.UUID,
    data: DocumentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_UPLOAD)),
):
    """更新文档元数据/解析后的文本内容（修订原 OCR/解析结果）。"""
    doc = await update_document(db, document_id, data, current_user)
    return success_response(data=doc.model_dump(mode="json"), message="文档已更新")


@router.delete("/documents/{document_id}", response_model=dict)
async def remove(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_DELETE)),
):
    """删除需求文档（同时删除磁盘文件）。"""
    await delete_document(db, document_id, current_user)
    return success_response(message="文档已删除")


# ── AI 评审 ──

@router.post("/documents/{document_id}/review", response_model=dict)
async def start_review(
    document_id: uuid.UUID,
    body: ReviewTriggerRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_REVIEW)),
):
    """触发 AI 评审需求文档，返回评审结果。"""
    llm_config_id = body.llm_config_id if body else None
    review = await trigger_review(db, document_id, current_user, llm_config_id)
    return success_response(data=review.model_dump(mode="json"), message="评审完成")


@router.get("/documents/{document_id}/reviews", response_model=dict)
async def list_document_reviews(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_VIEW)),
):
    """获取文档的所有历史评审记录。"""
    reviews = await list_reviews(db, document_id)
    return success_response(
        data=[r.model_dump(mode="json") for r in reviews]
    )


@router.get("/reviews/{review_id}", response_model=dict)
async def get_review_detail(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_VIEW)),
):
    """获取单次评审的详细结果。"""
    review = await get_review(db, review_id)
    return success_response(data=review.model_dump(mode="json"))


@router.delete("/reviews/{review_id}", response_model=dict)
async def remove_review(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.REQUIREMENT_DELETE)),
):
    """删除单次评审记录（仅评审发起者本人或超管）。"""
    await delete_review(db, review_id, current_user)
    return success_response(message="评审记录已删除")
