"""AI 需求评审服务：调用 LLM 对需求文档进行多维度评审。"""

import json
import logging
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.core.exceptions import AppException, NotFoundException
from app.modules.auth.models import User
from app.modules.llm.models import LLMConfig
from app.modules.llm.prompts.review import REVIEW_SYSTEM_PROMPT, build_review_user_prompt
from app.modules.llm.providers import build_client
from app.modules.requirements.models import AIReview, RequirementDocument
from app.modules.requirements.schemas import ReviewListResponse, ReviewResponse

logger = logging.getLogger(__name__)


def _to_review_response(review: AIReview) -> ReviewResponse:
    return ReviewResponse(
        id=review.id,
        document_id=review.document_id,
        reviewer_id=review.reviewer_id,
        reviewer_name=(
            review.reviewer.display_name or review.reviewer.username
            if review.reviewer else None
        ),
        llm_config_id=review.llm_config_id,
        llm_config_name=review.llm_config.name if review.llm_config else None,
        model_used=review.model_used,
        status=review.status,
        overall_score=review.overall_score,
        dimensions=review.dimensions,
        issues=review.issues,
        summary=review.summary,
        review_time_ms=review.review_time_ms,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def _to_list_response(review: AIReview) -> ReviewListResponse:
    return ReviewListResponse(
        id=review.id,
        document_id=review.document_id,
        reviewer_name=(
            review.reviewer.display_name or review.reviewer.username
            if review.reviewer else None
        ),
        model_used=review.model_used,
        status=review.status,
        overall_score=review.overall_score,
        summary=review.summary,
        review_time_ms=review.review_time_ms,
        created_at=review.created_at,
    )


async def trigger_review(
    db: AsyncSession,
    document_id: uuid.UUID,
    user: User,
    llm_config_id: uuid.UUID | None = None,
) -> ReviewResponse:
    doc = await _get_document_or_404(db, document_id)

    if not doc.content_text:
        raise AppException("文档尚未解析成功，无法评审", code="NO_CONTENT", status_code=422)

    config, api_key = await _resolve_llm_config(db, llm_config_id)

    review = AIReview(
        document_id=document_id,
        reviewer_id=user.id,
        llm_config_id=config.id,
        model_used=config.model,
        status="pending",
    )
    db.add(review)
    await db.flush()

    start_ms = _now_ms()
    try:
        result = await _call_llm_review(config, api_key, doc.filename, doc.content_text)

        review.overall_score = result.get("overall_score")
        review.dimensions = result.get("dimensions")
        review.issues = result.get("issues")
        review.summary = result.get("summary")
        review.raw_response = json.dumps(result, ensure_ascii=False)
        review.status = "completed"
    except Exception as e:
        logger.exception("AI review failed for document %s", document_id)
        review.status = "failed"
        review.raw_response = str(e)
    finally:
        review.review_time_ms = _now_ms() - start_ms

    await db.flush()
    await db.refresh(review)
    return _to_review_response(review)


async def get_review(db: AsyncSession, review_id: uuid.UUID) -> ReviewResponse:
    review = await _get_review_or_404(db, review_id)
    return _to_review_response(review)


async def delete_review(
    db: AsyncSession,
    review_id: uuid.UUID,
    user: User,
) -> None:
    """删除评审记录。仅触发者本人或超管可删除。"""
    review = await _get_review_or_404(db, review_id)
    if review.reviewer_id != user.id and not user.is_superuser:
        from app.core.exceptions import PermissionDeniedException
        raise PermissionDeniedException("只有评审发起者或超管可以删除")
    await db.delete(review)


async def list_reviews(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> list[ReviewListResponse]:
    result = await db.execute(
        select(AIReview)
        .where(AIReview.document_id == document_id)
        .order_by(AIReview.created_at.desc())
    )
    reviews = list(result.scalars().unique().all())
    return [_to_list_response(r) for r in reviews]


# ── Internal helpers ──

async def _call_llm_review(
    config: LLMConfig,
    api_key: str | None,
    filename: str,
    content_text: str,
) -> dict:
    client = build_client(config.provider, api_key, config.base_url)
    try:
        response = await client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": build_review_user_prompt(filename, content_text)},
            ],
            temperature=0.3,
            max_tokens=config.max_tokens or 4096,
        )
        raw = response.choices[0].message.content or ""
        return _parse_review_json(raw)
    finally:
        await client.close()


def _parse_review_json(raw: str) -> dict:
    cleaned = raw.strip()
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if json_match:
        cleaned = json_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            data = json.loads(json_match.group())
        else:
            raise AppException(
                "AI 返回的评审结果格式无法解析", code="PARSE_ERROR", status_code=502
            )

    required_keys = {"overall_score", "summary", "dimensions", "issues"}
    missing = required_keys - set(data.keys())
    if missing:
        raise AppException(
            f"AI 评审结果缺少字段: {', '.join(missing)}", code="INCOMPLETE_RESPONSE", status_code=502
        )

    return data


async def _resolve_llm_config(
    db: AsyncSession, config_id: uuid.UUID | None
) -> tuple[LLMConfig, str | None]:
    if config_id:
        result = await db.execute(select(LLMConfig).where(LLMConfig.id == config_id))
        config = result.scalar_one_or_none()
        if not config:
            raise NotFoundException("指定的 LLM 配置不存在")
    else:
        result = await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise AppException(
                "未配置默认 LLM，请先在设置中添加 LLM 配置或指定 llm_config_id",
                code="NO_LLM_CONFIG",
                status_code=422,
            )

    api_key = decrypt(config.api_key_encrypted) if config.api_key_encrypted else None
    return config, api_key


async def _get_document_or_404(db: AsyncSession, doc_id: uuid.UUID) -> RequirementDocument:
    result = await db.execute(
        select(RequirementDocument).where(RequirementDocument.id == doc_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise NotFoundException("需求文档不存在")
    return doc


async def _get_review_or_404(db: AsyncSession, review_id: uuid.UUID) -> AIReview:
    result = await db.execute(
        select(AIReview).where(AIReview.id == review_id)
    )
    review = result.scalar_one_or_none()
    if not review:
        raise NotFoundException("评审记录不存在")
    return review


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
