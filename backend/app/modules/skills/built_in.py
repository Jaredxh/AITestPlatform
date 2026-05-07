"""内置 ``system_*`` skill 同步（Task 12.4）。

MVP：3 条——1 条生效 UI 自动化 + 2 条 deprecated/manual 占位（一期意图快通道保留）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.skills.models import Skill, SkillSafetyScan, SkillVersion
from app.modules.skills.safety_scanner import SafetyScanner

SYSTEM_SKILLS_VERSION = "1.0"

_EXPECTED_SLUGS = frozenset({
    "system_ui_automation",
    "system_requirement_review",
    "system_testcase_generation",
})


@dataclass(frozen=True, slots=True)
class _BuiltinSpec:
    name: str
    slug: str
    description: str
    body: str
    triggers: list[str]
    tools_required: list[str]
    activation_mode: str
    category: str
    extra_metadata: dict


_BODY_UI = """# UI 自动化（内置）

通过 platform_* 工具检索当前项目用例与环境，并启动二期 UI ExecutionEngine。

## 何时使用

用户提到「跑 UI 测试」「跑用例」「自动化测试」「执行 UI 用例」或类似意图时使用。

## 执行顺序建议

1. 如需澄清目标，先 ``platform_search_testcases``。
2. 用 ``platform_list_environments`` 确认环境。
3. 最后 ``platform_run_ui_execution`` 批量启动执行。
"""

_BODY_REVIEW = """# 需求评审（兼容占位）

> **deprecated_path**：实际评审由一期 ``review_service`` / 对话意图快通道完成。
> 本 skill 仅用于 ClawHub 导出兼容与平台能力清单展示；**不要依赖本 skill 触发评审**。
"""

_BODY_GEN = """# 测试用例生成（兼容占位）

> **deprecated_path**：实际生成由一期生成意图快通道完成。
> 本 skill 仅用于导出兼容展示；**不要依赖本 skill 触发生成**。
"""


def _scan_notes(findings: list) -> str | None:
    if not findings:
        return None
    first = findings[0]
    if isinstance(first, dict):
        return str(first.get("snippet", ""))[:500]
    return None


BUILTIN_SPECS: tuple[_BuiltinSpec, ...] = (
    _BuiltinSpec(
        name="内置 · UI 自动化",
        slug="system_ui_automation",
        description="对话内检索用例与环境并启动 UI 自动化执行（platform_*）。",
        body=_BODY_UI,
        triggers=["跑 UI 测试", "跑用例", "自动化测试", "执行 UI 用例"],
        tools_required=[
            "platform_run_ui_execution",
            "platform_search_testcases",
            "platform_list_environments",
        ],
        activation_mode="agent_callable",
        category="system",
        extra_metadata={},
    ),
    _BuiltinSpec(
        name="内置 · 需求评审（占位）",
        slug="system_requirement_review",
        description="deprecated_path：评审走一期意图通道；本条目仅为兼容导出。",
        body=_BODY_REVIEW,
        triggers=[],
        tools_required=[],
        activation_mode="manual",
        category="system",
        extra_metadata={"deprecated_path": True},
    ),
    _BuiltinSpec(
        name="内置 · 用例生成（占位）",
        slug="system_testcase_generation",
        description="deprecated_path：生成走一期意图通道；本条目仅为兼容导出。",
        body=_BODY_GEN,
        triggers=[],
        tools_required=[],
        activation_mode="manual",
        category="system",
        extra_metadata={"deprecated_path": True},
    ),
)


def _bundle_meta(spec: _BuiltinSpec) -> dict:
    m = dict(spec.extra_metadata)
    m["_system_bundle_version"] = SYSTEM_SKILLS_VERSION
    m["_builtin_slug"] = spec.slug
    return m


async def sync_built_in_skills(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    created_by: uuid.UUID,
) -> int:
    """幂等：缺失或版本不一致时重写本项目全部内置 skill。返回新建条数。"""
    stmt = select(Skill).where(Skill.project_id == project_id, Skill.source == "built_in")
    existing = list((await db.execute(stmt)).scalars().all())

    need_rewrite = False
    if not existing:
        need_rewrite = True
    elif len(existing) != len(BUILTIN_SPECS):
        need_rewrite = True
    elif {s.slug for s in existing} != _EXPECTED_SLUGS:
        need_rewrite = True
    else:
        for s in existing:
            ver = (s.extra_metadata or {}).get("_system_bundle_version")
            if ver != SYSTEM_SKILLS_VERSION:
                need_rewrite = True
                break

    if not need_rewrite:
        return 0

    await db.execute(delete(Skill).where(Skill.project_id == project_id, Skill.source == "built_in"))
    await db.flush()

    scanner = SafetyScanner()
    created = 0
    for spec in BUILTIN_SPECS:
        scan = scanner.scan(spec.body, _bundle_meta(spec))
        findings = [f.as_dict() for f in scan.findings]
        scan_status = scan.status
        is_enabled = scan_status != "blocked"

        skill = Skill(
            project_id=project_id,
            name=spec.name[:200],
            slug=spec.slug[:100],
            description=spec.description,
            semantic_version="1.0.0",
            category=spec.category[:50],
            tags=[],
            triggers=list(spec.triggers),
            tools_required=list(spec.tools_required),
            activation_mode=spec.activation_mode,
            body=spec.body,
            extra_metadata=_bundle_meta(spec),
            attachments=[],
            source="built_in",
            source_url=None,
            is_enabled=is_enabled,
            safety_scan_status=scan_status,
            safety_scan_notes=_scan_notes(findings),
            db_version=1,
            created_by=created_by,
        )
        db.add(skill)
        await db.flush()

        sv = SkillVersion(
            skill_id=skill.id,
            db_version=skill.db_version,
            body=skill.body,
            extra_metadata=dict(skill.extra_metadata),
            change_note="built_in_sync",
            created_by=created_by,
        )
        db.add(sv)
        db.add(
            SkillSafetyScan(
                skill_id=skill.id,
                skill_db_version=skill.db_version,
                status=scan_status,
                findings=findings,
                scanner_version=SafetyScanner.VERSION,
            ),
        )
        created += 1

    await db.flush()
    return created
