import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── 文档相关 ──

class DocumentUploadResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    file_size: int
    content_type: str
    status: str
    content_text: str | None = None
    parse_error: str | None = Field(
        None,
        description="抽取文本失败时的错误信息；status=parse_failed 时填充。"
        "前端据此提示用户：自动解析失败，已保存原始文件，请手动编辑文本或换 .docx 上传。",
    )
    uploaded_by: uuid.UUID
    uploader_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    file_size: int
    content_type: str
    status: str
    uploaded_by: uuid.UUID
    uploader_name: str | None = None
    created_at: datetime
    updated_at: datetime
    review_status: str = Field(
        "unreviewed",
        description="评审状态：unreviewed/reviewing/reviewed/failed",
    )
    review_count: int = 0
    last_review_score: float | None = None

    model_config = {"from_attributes": True}


class DocumentDetailResponse(DocumentUploadResponse):
    content_preview: str | None = Field(None, description="文本内容前 500 字符预览")
    review_status: str = "unreviewed"
    review_count: int = 0
    last_review_score: float | None = None


class DocumentListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    search: str | None = None


class DocumentUpdateRequest(BaseModel):
    filename: str | None = Field(None, min_length=1, max_length=255)
    content_text: str | None = None


# ── AI 评审相关 ──

class ReviewTriggerRequest(BaseModel):
    llm_config_id: uuid.UUID | None = Field(None, description="指定 LLM 配置，为空则使用默认配置")


class ReviewDimensionScore(BaseModel):
    score: float = Field(..., ge=0, le=100)
    comment: str


class ReviewIssue(BaseModel):
    severity: str = Field(..., pattern=r"^(high|medium|low)$")
    category: str
    description: str
    location: str | None = None
    suggestion: str | None = None


class ReviewResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewer_name: str | None = None
    llm_config_id: uuid.UUID | None = None
    llm_config_name: str | None = None
    model_used: str | None = None
    status: str
    overall_score: float | None = None
    dimensions: dict[str, Any] | None = None
    issues: list[Any] | None = None
    summary: str | None = None
    review_time_ms: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewListResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    reviewer_name: str | None = None
    model_used: str | None = None
    status: str
    overall_score: float | None = None
    summary: str | None = None
    review_time_ms: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
