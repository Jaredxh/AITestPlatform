import uuid

from sqlalchemy import BigInteger, ForeignKey, Integer, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TestcaseModule(Base):
    """测试用例模块树节点。支持无限层级的树形结构。"""

    __tablename__ = "testcase_modules"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("testcase_modules.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 模块入口路径（可选）。配合 ``TestEnvironment.base_url`` 使用：
    # 执行时实际目标 URL = ``base_url + entry_path``。
    #
    # 设计目的：解决"同一系统多子模块、每个模块有不同入口页面"的常见场景
    # —— 让 AI 跑该模块下的用例前先 navigate 到这里。``None`` 表示该模块没
    # 配特殊入口，由用例步骤里的自然语言指令决定。
    #
    # 接受形式：
    #   - 绝对路径："/admin/users"、"/dashboard/analytics"
    #   - 完整 URL："https://other.example.com/x"（同 host 下的 path 跨子域）
    #   - 空串 / None：未配置（向后兼容）
    entry_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    project = relationship("Project", lazy="selectin")
    children: Mapped[list["TestcaseModule"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TestcaseModule.order_index",
    )
    parent: Mapped["TestcaseModule | None"] = relationship(
        back_populates="children", remote_side="TestcaseModule.id", lazy="selectin",
    )
    testcases: Mapped[list["Testcase"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", lazy="noload",
    )


class Testcase(Base):
    """测试用例。"""

    __tablename__ = "testcases"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 项目内递增的"用例编号"，前端用 TC-{case_no:04d} 渲染。
    # 与 UUID 主键并存：UUID 给后端用作稳定外键，case_no 给人看。
    # 唯一性由 (project_id, case_no) 复合唯一索引保证（在 alembic 里建）。
    case_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("testcase_modules.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    precondition: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(
        String(20), default="medium", server_default="medium",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active",
    )
    source: Mapped[str] = mapped_column(
        String(30), default="manual", server_default="manual",
    )
    # 执行结果：not_run / passed / failed / blocked
    exec_result: Mapped[str] = mapped_column(
        String(20), default="not_run", server_default="not_run",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_generation_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 二期 Task 8.5：用例级默认物料集（id 列表，uuid 字符串）。
    # 运行时与环境级 / 项目级 / 个人级 / 执行级合并，详见 Task 9.1。
    default_data_set_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )

    # Phase 13 / Task 13.2：用例标签集合（如 ["regression", "P0", "login"]）。
    # ui_automation skill 的 case_matcher 策略 2 用 tags 做语义召回——用户说
    # "回归用例" / "P0 用例" / "登录相关"时按此精确命中。GIN 索引在 alembic
    # ``d9b1c2e4f5a6_add_testcases_tags`` 中创建。前端 / 老 API 不强制使用，
    # 默认空数组与历史行为完全等价。
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"),
    )

    project = relationship("Project", lazy="selectin")
    module = relationship("TestcaseModule", back_populates="testcases", lazy="selectin")
    creator = relationship("User", lazy="selectin")
    steps: Mapped[list["TestcaseStep"]] = relationship(
        back_populates="testcase", cascade="all, delete-orphan", lazy="selectin",
        order_by="TestcaseStep.step_number",
    )


class TestcaseStep(Base):
    """测试用例步骤。"""

    __tablename__ = "testcase_steps"

    testcase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("testcases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text)

    testcase: Mapped["Testcase"] = relationship(back_populates="steps")


class AIGenerationBatch(Base):
    """AI 用例生成批次记录。"""

    __tablename__ = "ai_generation_batches"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirement_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("testcase_modules.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_configs.id"), nullable=True,
    )
    model_used: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), default="generating", server_default="generating",
    )
    generated_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    accepted_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    raw_response: Mapped[str | None] = mapped_column(Text)
    generation_time_ms: Mapped[int | None] = mapped_column(BigInteger)

    project = relationship("Project", lazy="selectin")
    document = relationship("RequirementDocument", lazy="selectin")
    module = relationship("TestcaseModule", lazy="selectin")
    user = relationship("User", lazy="selectin")
    llm_config = relationship("LLMConfig", lazy="selectin")
