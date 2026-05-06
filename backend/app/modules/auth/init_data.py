"""应用启动时初始化预置角色数据，并同步内置提示词模板。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.modules.auth.models import Role, User
from app.modules.auth.permissions import SYSTEM_ROLES

logger = logging.getLogger(__name__)


async def init_roles() -> None:
    async with async_session_factory() as session:
        await _seed_roles(session)
        await session.commit()


async def _seed_roles(db: AsyncSession) -> None:
    """创建 / 同步系统预置角色。

    对 ``is_system=True`` 的角色，每次启动都做"权限 / 描述"同步 —— 这样 code
    里新增的权限（如 Task 8.5 的 ``test_data:*``）会自动下发到已有部署，不必
    手动改数据库。非系统角色不在这里触碰。
    """
    result = await db.execute(select(Role).where(Role.is_system.is_(True)))
    existing: dict[str, Role] = {r.name: r for r in result.scalars().all()}

    for name, config in SYSTEM_ROLES.items():
        desired_perms = sorted(set(config["permissions"]))
        if name in existing:
            role = existing[name]
            changed = False
            if role.display_name != config["display_name"]:
                role.display_name = config["display_name"]
                changed = True
            if role.description != config["description"]:
                role.description = config["description"]
                changed = True
            current_perms = sorted(set(role.permissions or []))
            if current_perms != desired_perms:
                role.permissions = desired_perms
                changed = True
            if changed:
                logger.info("同步系统角色权限: %s", name)
            continue

        role = Role(
            name=name,
            display_name=config["display_name"],
            description=config["description"],
            permissions=desired_perms,
            is_system=True,
        )
        db.add(role)
        logger.info("创建系统角色: %s (%s)", name, config["display_name"])


async def sync_built_in_prompts() -> None:
    """对所有项目重新同步一次内置提示词，保证 ``built_in.py`` 的更新生效。"""
    from app.modules.projects.models import Project
    from app.modules.prompts.service import init_project_prompts

    async with async_session_factory() as session:
        super_user = (
            await session.execute(select(User).where(User.is_superuser.is_(True)).limit(1))
        ).scalar_one_or_none()
        if not super_user:
            return

        projects = list((await session.execute(select(Project))).scalars().all())
        for project in projects:
            try:
                await init_project_prompts(session, project.id, super_user.id)
            except Exception:  # noqa: BLE001
                logger.exception("同步项目 %s 的内置提示词失败", project.id)
        await session.commit()
        if projects:
            logger.info("已同步 %d 个项目的内置提示词模板", len(projects))
