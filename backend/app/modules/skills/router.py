"""Skill HTTP API（Task 12.4）。"""

from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.skills.importer import import_url, import_zip
from app.modules.skills.schemas import (
    MatchTriggerResponse,
    SkillCreateRequest,
    SkillImportUrlRequest,
    SkillManualChatActivateRequest,
    SkillMatchTriggersJsonRequest,
    SkillToggleRequest,
    SkillUpdateRequest,
)
from app.modules.skills.service import SkillService
from app.modules.skills.triggers import match_triggers_debug
from app.modules.skills.usage_service import aggregate_stats, daily_trend, list_failures

router = APIRouter(prefix="/api", tags=["技能包"])


@router.get("/projects/{project_id}/skills", response_model=dict)
async def list_project_skills(
    project_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    activation_mode: str | None = Query(None),
    source: str | None = Query(None),
    is_enabled: bool | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    items, total = await SkillService.list_skills(
        db,
        project_id,
        current_user,
        page=page,
        page_size=page_size,
        activation_mode=activation_mode,
        source=source,
        is_enabled=is_enabled,
        search=search,
    )
    return success_response(
        data={
            "items": [i.model_dump(mode="json") for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.post("/projects/{project_id}/skills", response_model=dict)
async def create_project_skill(
    project_id: uuid.UUID,
    data: SkillCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_EDIT)),
):
    skill = await SkillService.create_skill(db, project_id, data, current_user)
    return success_response(data=skill.model_dump(mode="json"), message="技能已创建")


@router.post("/projects/{project_id}/skills/import", response_model=dict)
async def import_skill_zip(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_IMPORT)),
):
    preview = await import_zip(db, project_id, file, current_user)
    return success_response(
        data={
            "preview": {
                "name": preview.name,
                "slug": preview.slug,
                "description": preview.description,
                "semantic_version": preview.semantic_version,
                "category": preview.category,
                "activation_mode": preview.activation_mode,
                "triggers": preview.triggers,
                "tools_required": preview.tools_required,
                "body_preview": preview.body_preview,
                "body_size_bytes": preview.body_size_bytes,
                "attachments": preview.attachments,
                "safety_status": preview.safety_status,
                "safety_findings": preview.safety_findings,
                "metadata_extra_keys": preview.metadata_extra_keys,
                "skill_id": str(preview.skill_id) if preview.skill_id else None,
            },
        },
        message="导入完成",
    )


@router.post("/projects/{project_id}/skills/import-url", response_model=dict)
async def import_skill_url(
    project_id: uuid.UUID,
    data: SkillImportUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_IMPORT)),
):
    skill = await import_url(db, project_id, data.url, current_user, ref=data.ref)
    detail = await SkillService.get_skill(db, skill.id, current_user)
    return success_response(data=detail.model_dump(mode="json"), message="已从 URL 导入")


@router.get("/projects/{project_id}/skills/active-for-chat", response_model=dict)
async def active_skills_for_chat(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    data = await SkillService.list_active_for_chat(db, project_id, current_user)
    return success_response(
        data={k: [x.model_dump(mode="json") for x in v] for k, v in data.items()},
    )


@router.get("/projects/{project_id}/skills/manual-for-chat", response_model=dict)
async def manual_skills_for_chat(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    rows = await SkillService.list_manual_for_chat(db, project_id, current_user)
    return success_response(data=[r.model_dump(mode="json") for r in rows])


@router.post("/projects/{project_id}/skills/chat/activate-manual", response_model=dict)
async def activate_manual_skills_chat(
    project_id: uuid.UUID,
    body: SkillManualChatActivateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_CHAT_ACTIVATE)),
):
    result = await SkillService.activate_manual_skills(
        db,
        project_id,
        current_user,
        session_id=body.session_id,
        manual_skill_ids=body.manual_skill_ids,
    )
    return success_response(data=result, message="已更新会话手动技能")


@router.post("/projects/{project_id}/skills/match-triggers", response_model=dict)
async def match_triggers_route(
    project_id: uuid.UUID,
    body: SkillMatchTriggersJsonRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    matches = await match_triggers_debug(
        db,
        project_id,
        body.message,
        max_matches=body.max,
    )
    payload = [
        MatchTriggerResponse(
            skill_id=s.id,
            name=s.name,
            slug=s.slug,
            score=score,
            matched_triggers=hits,
        )
        for s, score, hits in matches
    ]
    return success_response(data=[p.model_dump(mode="json") for p in payload])


@router.get("/projects/{project_id}/skills/usage-stats", response_model=dict)
async def skill_usage_stats(
    project_id: uuid.UUID,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    stats = await aggregate_stats(db, project_id, days=days)
    return success_response(data=stats)


@router.get("/projects/{project_id}/skills/usage-trend", response_model=dict)
async def skill_usage_trend(
    project_id: uuid.UUID,
    days: int = Query(30, ge=1, le=90),
    skill_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    """统计页趋势图：按天的调用量曲线。"""
    trend = await daily_trend(db, project_id, days=days, skill_id=skill_id)
    return success_response(data=trend)


@router.get("/skills/{skill_id}/failures", response_model=dict)
async def skill_failures(
    skill_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    """统计页失败明细抽屉：最近 N 条失败记录。"""
    # 校验当前用户对该 skill 所属项目的访问权（复用 SkillService 的成员检查）
    await SkillService.get_skill(db, skill_id, current_user)
    rows = await list_failures(db, skill_id, limit=limit)
    return success_response(data=rows)


@router.get("/skills/{skill_id}", response_model=dict)
async def get_skill_detail(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    skill = await SkillService.get_skill(db, skill_id, current_user)
    return success_response(data=skill.model_dump(mode="json"))


@router.get("/skills/{skill_id}/versions", response_model=dict)
async def list_skill_versions(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_VIEW)),
):
    versions = await SkillService.list_versions(db, skill_id, current_user)
    return success_response(data=[v.model_dump(mode="json") for v in versions])


@router.patch("/skills/{skill_id}", response_model=dict)
async def patch_skill(
    skill_id: uuid.UUID,
    data: SkillUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_EDIT)),
):
    skill = await SkillService.update_skill(db, skill_id, data, current_user)
    return success_response(data=skill.model_dump(mode="json"), message="技能已更新")


@router.delete("/skills/{skill_id}", response_model=dict)
async def remove_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_DELETE)),
):
    await SkillService.delete_skill(db, skill_id, current_user)
    return success_response(message="技能已删除")


@router.post("/skills/{skill_id}/toggle", response_model=dict)
async def toggle_skill(
    skill_id: uuid.UUID,
    body: SkillToggleRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_EDIT)),
):
    raw = body.is_enabled if body else None
    skill = await SkillService.toggle_skill(db, skill_id, current_user, is_enabled=raw)
    return success_response(data=skill.model_dump(mode="json"), message="状态已更新")


@router.post("/skills/{skill_id}/scan", response_model=dict)
async def scan_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_SCAN)),
):
    skill = await SkillService.rescan_skill(db, skill_id, current_user)
    return success_response(data=skill.model_dump(mode="json"), message="安全扫描已更新")


@router.get("/skills/{skill_id}/export")
async def export_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.SKILL_EXPORT)),
):
    slug, raw = await SkillService.export_skill_zip(db, skill_id, current_user)
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )
