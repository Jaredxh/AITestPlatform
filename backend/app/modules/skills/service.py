"""Skill CRUD 与辅助查询（Task 12.4）。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.llm.models import ChatSession
from app.modules.projects.service import _check_member_access, _get_project_or_404
from app.modules.skills.models import Skill, SkillSafetyScan, SkillVersion
from app.modules.skills.safety_scanner import SafetyScanner
from app.modules.skills.schemas import (
    SkillCreateRequest,
    SkillListResponse,
    SkillResponse,
    SkillUpdateRequest,
    SkillVersionResponse,
)


def _scan_notes(findings: list) -> str | None:
    if not findings:
        return None
    first = findings[0]
    if isinstance(first, dict):
        return str(first.get("snippet", ""))[:500]
    return None


async def list_always_skills(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 2,
) -> list[Skill]:
    stmt = (
        select(Skill)
        .where(
            Skill.project_id == project_id,
            Skill.is_enabled.is_(True),
            Skill.activation_mode == "always",
        )
        .order_by(Skill.updated_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_agent_callable_skills(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 5,
) -> list[Skill]:
    stmt = (
        select(Skill)
        .where(
            Skill.project_id == project_id,
            Skill.is_enabled.is_(True),
            Skill.activation_mode == "agent_callable",
        )
        .order_by(Skill.updated_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


class SkillService:
    """项目内 Skill CRUD；内置条目禁止删除。"""

    @staticmethod
    async def _ensure_project_member(db: AsyncSession, project_id: uuid.UUID, user: User) -> None:
        project = await _get_project_or_404(db, project_id)
        _check_member_access(project, user)

    @staticmethod
    async def _get_skill_in_project(
        db: AsyncSession,
        skill_id: uuid.UUID,
        user: User,
    ) -> Skill:
        skill = await db.get(Skill, skill_id)
        if skill is None or skill.project_id is None:
            raise NotFoundException("技能不存在")
        await SkillService._ensure_project_member(db, skill.project_id, user)
        return skill

    @staticmethod
    async def list_skills(
        db: AsyncSession,
        project_id: uuid.UUID,
        user: User,
        *,
        page: int = 1,
        page_size: int = 20,
        activation_mode: str | None = None,
        source: str | None = None,
        is_enabled: bool | None = None,
        search: str | None = None,
    ) -> tuple[list[SkillListResponse], int]:
        await SkillService._ensure_project_member(db, project_id, user)
        base = select(Skill).where(Skill.project_id == project_id)
        if activation_mode:
            base = base.where(Skill.activation_mode == activation_mode)
        if source:
            base = base.where(Skill.source == source)
        if is_enabled is not None:
            base = base.where(Skill.is_enabled.is_(bool(is_enabled)))
        if search:
            pat = f"%{search.strip()}%"
            base = base.where(or_(Skill.name.ilike(pat), Skill.slug.ilike(pat)))

        count_stmt = select(func.count()).select_from(base.subquery())
        total = int((await db.execute(count_stmt)).scalar() or 0)

        stmt = base.order_by(Skill.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = list((await db.execute(stmt)).scalars().all())
        return [SkillListResponse.model_validate(s) for s in rows], total

    @staticmethod
    async def get_skill(
        db: AsyncSession,
        skill_id: uuid.UUID,
        user: User,
    ) -> SkillResponse:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def get_by_slug(
        db: AsyncSession,
        project_id: uuid.UUID,
        slug: str,
        user: User,
    ) -> SkillResponse:
        await SkillService._ensure_project_member(db, project_id, user)
        stmt = select(Skill).where(Skill.project_id == project_id, Skill.slug == slug)
        skill = (await db.execute(stmt)).scalar_one_or_none()
        if skill is None:
            raise NotFoundException("技能不存在")
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def create_skill(
        db: AsyncSession,
        project_id: uuid.UUID,
        data: SkillCreateRequest,
        user: User,
    ) -> SkillResponse:
        await SkillService._ensure_project_member(db, project_id, user)
        scanner = SafetyScanner()
        scan = scanner.scan(data.body, dict(data.metadata))
        findings = [f.as_dict() for f in scan.findings]
        scan_status = scan.status
        if scan_status == "blocked":
            raise AppException(
                "内容未通过安全扫描，无法创建",
                code="SKILL_SCAN_BLOCKED",
                status_code=400,
            )

        skill = Skill(
            project_id=project_id,
            name=data.name[:200],
            slug=data.slug[:100],
            description=data.description,
            semantic_version=data.semantic_version[:20],
            category=data.category[:50],
            tags=list(data.tags),
            triggers=list(data.triggers),
            tools_required=list(data.tools_required),
            activation_mode=data.activation_mode,
            body=data.body,
            extra_metadata=dict(data.metadata),
            attachments=list(data.attachments),
            source="custom",
            source_url=None,
            is_enabled=scan_status == "clean",
            safety_scan_status=scan_status,
            safety_scan_notes=_scan_notes(findings),
            db_version=1,
            created_by=user.id,
        )
        db.add(skill)
        await db.flush()

        db.add(
            SkillVersion(
                skill_id=skill.id,
                db_version=skill.db_version,
                body=skill.body,
                extra_metadata=dict(skill.extra_metadata),
                change_note="create",
                created_by=user.id,
            ),
        )
        db.add(
            SkillSafetyScan(
                skill_id=skill.id,
                skill_db_version=skill.db_version,
                status=scan_status,
                findings=findings,
                scanner_version=SafetyScanner.VERSION,
            ),
        )
        await db.flush()
        await db.refresh(skill)
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def update_skill(
        db: AsyncSession,
        skill_id: uuid.UUID,
        data: SkillUpdateRequest,
        user: User,
    ) -> SkillResponse:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)

        dirty_keys = {
            k
            for k, v in data.model_dump(exclude_unset=True).items()
            if k != "change_note" and v is not None
        }
        bump_version = bool(dirty_keys) and dirty_keys != {"is_enabled"}
        note = data.change_note or ("update" if bump_version else None)

        if data.name is not None:
            skill.name = data.name[:200]
        if data.description is not None:
            skill.description = data.description
        if data.semantic_version is not None:
            skill.semantic_version = data.semantic_version[:20]
        if data.category is not None:
            skill.category = data.category[:50]
        if data.tags is not None:
            skill.tags = list(data.tags)
        if data.triggers is not None:
            skill.triggers = list(data.triggers)
        if data.tools_required is not None:
            skill.tools_required = list(data.tools_required)
        if data.activation_mode is not None:
            skill.activation_mode = data.activation_mode
        if data.body is not None:
            skill.body = data.body
        if data.metadata is not None:
            skill.extra_metadata = dict(data.metadata)
        if data.attachments is not None:
            skill.attachments = list(data.attachments)
        if data.is_enabled is not None:
            skill.is_enabled = data.is_enabled

        if bump_version:
            scanner = SafetyScanner()
            scan = scanner.scan(skill.body, skill.extra_metadata or {})
            findings = [f.as_dict() for f in scan.findings]
            scan_status = scan.status
            skill.safety_scan_status = scan_status
            skill.safety_scan_notes = _scan_notes(findings)
            if scan_status == "blocked":
                skill.is_enabled = False
            skill.db_version = int(skill.db_version or 1) + 1

            db.add(
                SkillVersion(
                    skill_id=skill.id,
                    db_version=skill.db_version,
                    body=skill.body,
                    extra_metadata=dict(skill.extra_metadata),
                    change_note=note,
                    created_by=user.id,
                ),
            )
            db.add(
                SkillSafetyScan(
                    skill_id=skill.id,
                    skill_db_version=skill.db_version,
                    status=scan_status,
                    findings=findings,
                    scanner_version=SafetyScanner.VERSION,
                ),
            )

        await db.flush()
        await db.refresh(skill)
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def delete_skill(db: AsyncSession, skill_id: uuid.UUID, user: User) -> None:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)
        if skill.source == "built_in":
            raise PermissionDeniedException("内置技能不可删除")
        await db.delete(skill)

    @staticmethod
    async def toggle_skill(
        db: AsyncSession,
        skill_id: uuid.UUID,
        user: User,
        *,
        is_enabled: bool | None,
    ) -> SkillResponse:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)
        if is_enabled is None:
            skill.is_enabled = not skill.is_enabled
        else:
            skill.is_enabled = bool(is_enabled)
        await db.flush()
        await db.refresh(skill)
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def list_versions(
        db: AsyncSession,
        skill_id: uuid.UUID,
        user: User,
    ) -> list[SkillVersionResponse]:
        await SkillService._get_skill_in_project(db, skill_id, user)
        stmt = (
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.db_version.desc())
        )
        rows = list((await db.execute(stmt)).scalars().all())
        return [SkillVersionResponse.model_validate(v) for v in rows]

    @staticmethod
    async def rescan_skill(db: AsyncSession, skill_id: uuid.UUID, user: User) -> SkillResponse:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)
        scanner = SafetyScanner()
        scan = scanner.scan(skill.body, skill.extra_metadata or {})
        findings = [f.as_dict() for f in scan.findings]
        scan_status = scan.status
        skill.safety_scan_status = scan_status
        skill.safety_scan_notes = _scan_notes(findings)
        if scan_status == "blocked":
            skill.is_enabled = False

        db.add(
            SkillSafetyScan(
                skill_id=skill.id,
                skill_db_version=skill.db_version,
                status=scan_status,
                findings=findings,
                scanner_version=SafetyScanner.VERSION,
            ),
        )
        await db.flush()
        await db.refresh(skill)
        return SkillResponse.model_validate(skill)

    @staticmethod
    async def list_manual_for_chat(
        db: AsyncSession,
        project_id: uuid.UUID,
        user: User,
    ) -> list[SkillListResponse]:
        await SkillService._ensure_project_member(db, project_id, user)
        stmt = (
            select(Skill)
            .where(
                Skill.project_id == project_id,
                Skill.is_enabled.is_(True),
                Skill.activation_mode == "manual",
            )
            .order_by(Skill.updated_at.desc())
            .limit(50)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        return [SkillListResponse.model_validate(s) for s in rows]

    @staticmethod
    async def list_active_for_chat(
        db: AsyncSession,
        project_id: uuid.UUID,
        user: User,
    ) -> dict[str, list[SkillListResponse]]:
        await SkillService._ensure_project_member(db, project_id, user)
        always = await list_always_skills(db, project_id, limit=2)
        agent = await list_agent_callable_skills(db, project_id, limit=5)
        return {
            "always": [SkillListResponse.model_validate(s) for s in always],
            "agent_callable": [SkillListResponse.model_validate(s) for s in agent],
        }

    @staticmethod
    async def activate_manual_skills(
        db: AsyncSession,
        project_id: uuid.UUID,
        user: User,
        *,
        session_id: uuid.UUID,
        manual_skill_ids: list[uuid.UUID],
    ) -> dict[str, list[str]]:
        await SkillService._ensure_project_member(db, project_id, user)
        sess = await db.get(ChatSession, session_id)
        if sess is None:
            raise NotFoundException("会话不存在")
        if sess.user_id != user.id:
            raise PermissionDeniedException("无权修改该会话")
        if sess.project_id != project_id:
            raise PermissionDeniedException("会话不属于该项目")

        if manual_skill_ids:
            stmt = select(Skill.id).where(
                Skill.project_id == project_id,
                Skill.id.in_(manual_skill_ids),
            )
            found = {row[0] for row in (await db.execute(stmt)).all()}
            if found != set(manual_skill_ids):
                raise AppException(
                    "存在不属于该项目的技能 id",
                    code="SKILL_SESSION_MISMATCH",
                    status_code=400,
                )

        ctx = dict(sess.chat_context or {})
        ctx["manual_skill_ids"] = [str(i) for i in manual_skill_ids]
        sess.chat_context = ctx
        await db.flush()
        return {"manual_skill_ids": ctx["manual_skill_ids"]}

    @staticmethod
    async def export_skill_zip(
        db: AsyncSession,
        skill_id: uuid.UUID,
        user: User,
    ) -> tuple[str, bytes]:
        skill = await SkillService._get_skill_in_project(db, skill_id, user)
        from app.modules.skills.exporter import export_skill_as_zip

        return skill.slug, export_skill_as_zip(skill)
