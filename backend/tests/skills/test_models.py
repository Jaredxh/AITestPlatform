"""Skill ORM / Schema 字段约束测试（Phase 12 / Task 12.1）。

不依赖真实 DB —— 全部通过 SQLAlchemy ``inspect`` + DDL 字符串渲染断言。
真实 DB 的迁移成功性由 ``alembic upgrade head`` 验证（CI / 本地手动）。

覆盖：
- 4 张表全部出现在 ``Base.metadata.tables``
- 关键字段类型 / 默认值 / 是否可空
- CHECK 约束（activation_mode / source / safety_scan_status / outcome / status）
- UNIQUE (project_id, slug) + UNIQUE (skill_id, db_version)
- 索引名按设计文档命名
- ``Skill.metadata`` 在 ORM 上叫 ``extra_metadata``（避开 DeclarativeBase 保留属性）
- ``SkillCreateRequest`` 对 activation_mode / slug pattern 的 schema 校验
- ``SkillResponse`` / ``SkillVersionResponse`` 能从 ORM 实例反读 ``extra_metadata`` 输出为 ``metadata``
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.base import Base
from app.modules.skills.models import (
    Skill,
    SkillSafetyScan,
    SkillUsageLog,
    SkillVersion,
)
from app.modules.skills.schemas import (
    ACTIVATION_MODES,
    SkillCreateRequest,
    SkillResponse,
    SkillUpdateRequest,
    SkillVersionResponse,
)

# ──────────────────────────────────────────────────────────────────────────────
# 1) 表存在性 + 列存在性
# ──────────────────────────────────────────────────────────────────────────────


def test_all_four_tables_registered_on_metadata() -> None:
    table_names = set(Base.metadata.tables.keys())
    assert {
        "skills",
        "skill_versions",
        "skill_usage_logs",
        "skill_safety_scans",
    }.issubset(table_names)


def test_skills_table_columns_match_design() -> None:
    expected = {
        "id", "created_at", "updated_at",
        "project_id", "name", "slug", "description",
        "semantic_version", "category",
        "tags", "triggers", "tools_required", "activation_mode",
        "body", "metadata", "attachments",
        "source", "source_url", "is_enabled",
        "safety_scan_status", "safety_scan_notes",
        "db_version", "created_by",
    }
    cols = {c.name for c in Skill.__table__.columns}
    assert expected == cols


def test_skill_versions_table_columns() -> None:
    expected = {
        "id", "created_at", "updated_at",
        "skill_id", "db_version", "body", "metadata",
        "change_note", "created_by",
    }
    cols = {c.name for c in SkillVersion.__table__.columns}
    assert expected == cols


def test_skill_usage_logs_table_columns() -> None:
    expected = {
        "id", "created_at", "updated_at",
        "skill_id", "skill_db_version", "session_id", "message_id",
        "activation_reason", "matched_trigger", "tokens_consumed",
        "outcome", "error_message",
    }
    cols = {c.name for c in SkillUsageLog.__table__.columns}
    assert expected == cols


def test_skill_safety_scans_table_columns() -> None:
    expected = {
        "id", "created_at", "updated_at",
        "skill_id", "skill_db_version", "status",
        "findings", "scanner_version", "scanned_at",
    }
    cols = {c.name for c in SkillSafetyScan.__table__.columns}
    assert expected == cols


# ──────────────────────────────────────────────────────────────────────────────
# 2) ORM 属性别名：extra_metadata ↔ DB column metadata
# ──────────────────────────────────────────────────────────────────────────────


def test_orm_attribute_extra_metadata_maps_to_db_column_metadata() -> None:
    mapper = inspect(Skill)
    prop = mapper.attrs["extra_metadata"]
    cols = list(prop.columns)
    assert len(cols) == 1 and cols[0].name == "metadata"


def test_skill_metadata_attribute_does_not_shadow_declarative_metadata() -> None:
    # DeclarativeBase 自身的 ``metadata`` 句柄必须仍可用（不能被 ORM 列遮盖）
    assert Skill.metadata is Base.metadata
    assert "skills" in Skill.metadata.tables


# ──────────────────────────────────────────────────────────────────────────────
# 3) 约束（CHECK / UNIQUE / 索引）
# ──────────────────────────────────────────────────────────────────────────────


def _ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_skill_activation_mode_check_constraint_present() -> None:
    ddl = _ddl(Skill.__table__)
    assert "ck_skills_activation_mode" in ddl
    for mode in ACTIVATION_MODES:
        assert f"'{mode}'" in ddl


def test_skill_source_check_constraint_present() -> None:
    ddl = _ddl(Skill.__table__)
    assert "ck_skills_source" in ddl
    for s in ("'built_in'", "'imported'", "'custom'"):
        assert s in ddl


def test_skill_safety_status_check_constraint_present() -> None:
    ddl = _ddl(Skill.__table__)
    assert "ck_skills_safety_scan_status" in ddl
    for s in ("'unscanned'", "'clean'", "'warning'", "'blocked'"):
        assert s in ddl


def test_skill_unique_project_slug() -> None:
    ddl = _ddl(Skill.__table__)
    assert "uq_skills_project_slug" in ddl
    assert "UNIQUE" in ddl


def test_skill_versions_unique_skill_dbversion() -> None:
    ddl = _ddl(SkillVersion.__table__)
    assert "uq_skill_versions_skill_dbver" in ddl


def test_skill_usage_logs_check_constraints_present() -> None:
    ddl = _ddl(SkillUsageLog.__table__)
    assert "ck_skill_usage_activation_reason" in ddl
    assert "ck_skill_usage_outcome" in ddl
    for s in ("'manual'", "'trigger_match'", "'agent_callable'", "'always'", "'auto_apply'"):
        assert s in ddl
    for s in ("'success'", "'failed'", "'no_output'", "'user_cancelled'"):
        assert s in ddl


def test_skill_safety_scans_check_status_present() -> None:
    ddl = _ddl(SkillSafetyScan.__table__)
    assert "ck_skill_safety_status" in ddl
    for s in ("'clean'", "'warning'", "'blocked'"):
        assert s in ddl


def test_indices_named_per_design_doc() -> None:
    skill_indexes = {idx.name for idx in Skill.__table__.indexes}
    assert "idx_skills_project_enabled" in skill_indexes
    assert "idx_skills_activation_mode" in skill_indexes

    sv_indexes = {idx.name for idx in SkillVersion.__table__.indexes}
    assert "idx_skill_versions_skill" in sv_indexes

    usage_indexes = {idx.name for idx in SkillUsageLog.__table__.indexes}
    assert "idx_skill_usage_skill_time" in usage_indexes
    assert "idx_skill_usage_session" in usage_indexes

    safety_indexes = {idx.name for idx in SkillSafetyScan.__table__.indexes}
    assert "idx_skill_safety_skill" in safety_indexes


# ──────────────────────────────────────────────────────────────────────────────
# 4) 默认值（server-side ``server_default`` + python-side ``default``）
#
# 注：SQLAlchemy 2.x 中 ``mapped_column(default=...)`` 仅在 flush 时应用，
# 构造函数不会立刻填值，因此这里走"列声明反射"，验证默认值已正确登记到列定义。
# ──────────────────────────────────────────────────────────────────────────────


def _col(table, name):
    return table.columns[name]


def test_skill_jsonb_list_columns_have_server_default_empty_array() -> None:
    for col_name in ("tags", "triggers", "tools_required", "attachments"):
        col = _col(Skill.__table__, col_name)
        assert col.server_default is not None
        assert "'[]'::jsonb" in str(col.server_default.arg)
        # python-side default 也注册了，flush 时兜底。SQLAlchemy 会把 list / dict
        # 这种 callable 包成 CallableColumnDefault 的 wrapper，签名变成
        # ``wrapper(ctx)``；调用时传一个 mock ctx 即可。
        assert col.default is not None
        assert col.default.is_callable
        assert col.default.arg(None) == []


def test_skill_metadata_column_has_server_default_empty_object() -> None:
    col = _col(Skill.__table__, "metadata")
    assert col.server_default is not None
    assert "'{}'::jsonb" in str(col.server_default.arg)
    assert col.default is not None
    assert col.default.is_callable
    assert col.default.arg(None) == {}


def test_skill_scalar_columns_have_expected_server_defaults() -> None:
    table = Skill.__table__
    assert "'1.0.0'" in str(_col(table, "semantic_version").server_default.arg)
    assert "'custom'" in str(_col(table, "category").server_default.arg)
    assert "'agent_callable'" in str(_col(table, "activation_mode").server_default.arg)
    assert "'custom'" in str(_col(table, "source").server_default.arg)
    assert "'unscanned'" in str(_col(table, "safety_scan_status").server_default.arg)
    assert "true" in str(_col(table, "is_enabled").server_default.arg).lower()
    assert "1" == str(_col(table, "db_version").server_default.arg).strip()


def test_skill_version_metadata_has_server_default_empty_object() -> None:
    col = _col(SkillVersion.__table__, "metadata")
    assert col.server_default is not None
    assert "'{}'::jsonb" in str(col.server_default.arg)


def test_skill_safety_scan_scanned_at_has_server_default_now() -> None:
    col = _col(SkillSafetyScan.__table__, "scanned_at")
    assert col.server_default is not None
    # 渲染后形如 ``now()`` 或包含同名 func
    assert "now" in str(col.server_default.arg).lower()


# ──────────────────────────────────────────────────────────────────────────────
# 5) Pydantic schemas 校验
# ──────────────────────────────────────────────────────────────────────────────


def test_skill_create_request_minimal_payload() -> None:
    req = SkillCreateRequest(
        name="My Skill",
        slug="my_skill",
        description="d",
        body="b",
    )
    assert req.activation_mode == "agent_callable"
    assert req.semantic_version == "1.0.0"
    assert req.metadata == {}
    assert req.tools_required == []


def test_skill_create_request_rejects_invalid_activation_mode() -> None:
    with pytest.raises(ValidationError):
        SkillCreateRequest(
            name="x", slug="x", description="d", body="b",
            activation_mode="never",
        )


def test_skill_create_request_rejects_system_prefix_slug() -> None:
    with pytest.raises(ValidationError):
        SkillCreateRequest(
            name="x", slug="system_anything", description="d", body="b",
        )


def test_skill_create_request_rejects_invalid_slug_pattern() -> None:
    bad_slugs = ["My Skill", "WithCaps", "white space", "-leading", "trailing-"]
    for slug in bad_slugs:
        with pytest.raises(ValidationError):
            SkillCreateRequest(name="x", slug=slug, description="d", body="b")


def test_skill_create_request_accepts_kebab_and_snake_slugs() -> None:
    for slug in ["my_skill", "my-skill", "skill-1", "skill_1_0"]:
        req = SkillCreateRequest(name="x", slug=slug, description="d", body="b")
        assert req.slug == slug


def test_skill_update_request_partial_validation() -> None:
    upd = SkillUpdateRequest(triggers=["跑用例", "执行 UI 测试"])
    assert upd.name is None
    assert upd.activation_mode is None
    assert upd.triggers == ["跑用例", "执行 UI 测试"]


def test_skill_update_rejects_bad_activation_mode() -> None:
    with pytest.raises(ValidationError):
        SkillUpdateRequest(activation_mode="bogus")


def test_skill_response_reads_extra_metadata_from_orm_instance() -> None:
    """ORM 实例 (extra_metadata 属性) → Pydantic 响应字段 (metadata)。

    构造时显式填全所有 list/dict 字段：``mapped_column(default=...)`` 的
    python-side default 仅在 flush 时应用，构造函数不会立刻写入；测试
    模拟 fully populated row 状态。
    """
    s = Skill(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="x", slug="x_skill", description="d",
        body="b",
        tags=[], triggers=[], tools_required=[], attachments=[],
        extra_metadata={"author": "alice", "homepage": "https://example.com"},
        created_by=uuid.uuid4(),
    )
    s.created_at = datetime.now(tz=timezone.utc)
    s.updated_at = datetime.now(tz=timezone.utc)
    s.semantic_version = "1.0.0"
    s.category = "custom"
    s.activation_mode = "agent_callable"
    s.source = "custom"
    s.safety_scan_status = "unscanned"
    s.is_enabled = True
    s.db_version = 1

    resp = SkillResponse.model_validate(s)
    dumped = resp.model_dump()
    assert dumped["metadata"] == {"author": "alice", "homepage": "https://example.com"}
    assert "extra_metadata" not in dumped


def test_skill_version_response_reads_extra_metadata_from_orm_instance() -> None:
    sv = SkillVersion(
        id=uuid.uuid4(),
        skill_id=uuid.uuid4(),
        db_version=2,
        body="b",
        extra_metadata={"diff": {"changed": ["body"]}},
        change_note="tweak",
        created_by=uuid.uuid4(),
    )
    sv.created_at = datetime.now(tz=timezone.utc)
    sv.updated_at = datetime.now(tz=timezone.utc)

    resp = SkillVersionResponse.model_validate(sv)
    dumped = resp.model_dump()
    assert dumped["metadata"] == {"diff": {"changed": ["body"]}}
    assert dumped["change_note"] == "tweak"


def test_skill_response_accepts_metadata_input_directly() -> None:
    """从 dict 构造时，``metadata`` 字段也能正常读取（populate_by_name + alias choices）。"""
    payload = {
        "id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "name": "x", "slug": "x_skill", "description": "d",
        "semantic_version": "1.0.0", "category": "custom",
        "tags": [], "triggers": [], "tools_required": [],
        "activation_mode": "agent_callable",
        "body": "b",
        "metadata": {"k": "v"},
        "attachments": [],
        "source": "custom", "source_url": None,
        "is_enabled": True,
        "safety_scan_status": "unscanned", "safety_scan_notes": None,
        "db_version": 1,
        "created_by": str(uuid.uuid4()),
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    resp = SkillResponse.model_validate(payload)
    assert resp.metadata == {"k": "v"}
