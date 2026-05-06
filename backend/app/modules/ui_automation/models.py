"""UI 自动化模块的 SQLAlchemy ORM 模型。

Task 8.1 引入两张表：
- ``ui_test_environments``：测试环境配置（base_url / 域名白名单 / token 预算
  / 默认物料集等），自然满足 ``security.EnvironmentLike`` Protocol，能直
  接传给 ``BrowserBundle.open / SecurityGuard``。
- ``ui_precondition_templates``：环境下的前置步骤模板（state_inject /
  ai_login / scripted_steps / cookie_inject 四种类型）。

Task 9.5 引入三张执行结果表：
- ``ui_executions``：单次执行批次（含状态 / 模式 / 计数 / 物料快照）
- ``ui_case_results``：用例级结果（含 data_confidence / synthesized_data /
  data_failures，与 ``status`` 正交）
- ``ui_step_results``：步骤级结果（含 tool_calls / snapshot / assertion 判定）

后续 task 引入的表（不在当前 task 范围）：
- Task 8.4：``test_data_sets`` / ``test_data_items`` ✅ 已建

字段命名约定：
- 时间字段：``state_saved_at`` / ``saved_at``，统一 ``DateTime(timezone=True)``
- JSONB 列表字段：用 ``server_default=text("'[]'::jsonb")``，不要默认 None
- 加密字段：以 ``_encrypted`` 结尾，写库前 Fernet，读出来时按需 decrypt
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.modules.projects.models import Project


# ─── PreconditionTemplate type 常量 ───────────────────────────────────
# 与 PHASE2_DESIGN §2.4 保持一致；放在 model 模块顶层方便 Task 8.2 执行
# 器引用。
#
# **改这个集合时必须四处同步**（漏一个就 500 / 422 / dispatch 报错）：
#   1) models.PRECONDITION_TYPES（本变量）
#   2) schemas.PRECONDITION_TYPE_PATTERN（Pydantic 正则）
#   3) precondition_executor._dispatch 分支（运行时分发）
#   4) DB CHECK 约束 ``ck_ui_precondition_template_type``
#      —— 加新值要写一条 alembic migration（DROP+ADD 旧约束）
PRECONDITION_TYPES = (
    "state_inject", "ai_login", "scripted_steps", "cookie_inject",
    # http_login（Task 8.2.5）：纯 HTTP API 登录，免浏览器免 LLM。
    # 适用场景：后台暴露 ``/auth/getCode`` + ``/auth/login`` 这种"两段式 API
    # 鉴权"——前者 GET 拿挑战 cookie（图形验证码值实际就在 Set-Cookie 里），
    # 后者 POST 拿 token cookie（c_token）。比 ai_login 快 100 倍（<2s vs 60-180s）
    # 且 0 LLM 消耗、0 OCR 误差。
    # 实测同一公司多业务线（keyuanjiankang / weimiaocaishang / changqingjiankang）
    # 共用此鉴权服务，一次配好通行三套域名。
    "http_login",
)


class TestEnvironment(Base):
    """测试环境配置。

    一个 project 下可有多个环境（典型：dev / staging / prod-readonly）。
    与 ``app.modules.ui_automation.security.EnvironmentLike`` Protocol 完
    全契约一致 —— ``SecurityGuard(environment=env_orm)`` 直接能跑。
    """

    __tablename__ = "ui_test_environments"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # ── 浏览器目标 ───────────────────────────────────────────
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    """测试目标根 URL（含 scheme），例如 ``https://staging.foo.com``。
    创建时会自动从这里提取 hostname 加入 allowed_hosts。"""

    allowed_hosts: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    """放行的 host 列表。支持精确域名 / 通配符 ``*.foo.com`` / 含端口
    （详见 ``security.py`` 注释）。"""

    # ── 预算 / 安全开关 ──────────────────────────────────────
    token_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("50000"),
    )
    enable_browser_evaluate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    # ── BrowserContext 复用 ──────────────────────────────────
    session_name: Mapped[str | None] = mapped_column(String(100))
    """同名 session 之间共享 storage_state 文件，便于"多个环境共用同一
    套登录"的场景（state_manager 用此字段决定文件名 fallback）。
    None 时回退到 environment_id 命名。"""

    state_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """最近一次写入 storage_state 文件的时间。``POST /clear-state``
    时会清成 NULL；执行完登录类前置步骤后由 Task 8.2 写时间戳。"""

    # ── 物料默认值 ───────────────────────────────────────────
    default_data_set_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    """默认随执行加载的物料集 ID 列表（uuid 字符串）。Task 8.4 实现
    后这里指向 ``test_data_sets.id``；本 task 不做外键约束，避免
    循环依赖（物料表还没建）。"""

    # ── 浏览器外观 ───────────────────────────────────────────
    headless: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    viewport_width: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1280"),
    )
    viewport_height: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("800"),
    )

    # ── 关系 ────────────────────────────────────────────────
    project: Mapped["Project"] = relationship(lazy="selectin")
    preconditions: Mapped[list["PreconditionTemplate"]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        order_by="PreconditionTemplate.order_index",
        lazy="selectin",
    )


class PreconditionTemplate(Base):
    """环境下的前置步骤模板。每条对应一种 type；多条按 ``order_index`` 顺序执行。

    四种 type 的 ``config`` JSONB 形状（明文）：
    - ``state_inject``：``{}`` 或 ``{"required": true}``。无需额外配置，纯
      引用 environment.state_saved_at 对应的 storage_state 文件。
    - ``ai_login``：``{"login_url": "/login", "success_indicator": "...",
      "max_steps": 10}``。AI 需要登录指引 + 成功标志。
    - ``scripted_steps``：``{"steps": [{"action": "click", "selector": "..."},
      ...]}``。Playwright SDK 直接跑确定性脚本。
    - ``cookie_inject``：``{"cookies": [{"name": "...", "value_ref":
      "credentials.session_cookie", "domain": "...", "path": "/"}]}``。
      value 通常引用 credentials_encrypted 内的字段。

    ``credentials_encrypted`` 是 Fernet 加密后的 JSON 字符串（含
    username/password/cookie/token 等），任何敏感凭据**不允许**写在
    config 里。
    """

    __tablename__ = "ui_precondition_templates"

    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ui_test_environments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)

    order_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )

    state_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """前置步骤维度的 state 时间戳（与环境维度同名字段独立）。某些工作流
    需要"单条前置步骤产生 state 后立刻 mark"，由 Task 8.2 写入。"""

    environment: Mapped[TestEnvironment] = relationship(back_populates="preconditions")


# ─── Task 9.5：执行结果三张表 ──────────────────────────────────────────


# UIExecution 状态枚举（与设计文档 §4.1 对齐）
EXECUTION_STATUSES = (
    "pending",         # 创建后 Engine 未开跑
    "running",         # Engine 正在跑
    "completed",       # 全部用例跑完（含 passed / failed 混合）
    "stopped",         # 用户主动停止
    "failed",          # Engine 入口异常 / 严格模式拒绝执行
    "aborted_budget",  # token 预算耗尽，后续用例跳过
)

EXECUTION_MODES = ("normal", "debug")

CASE_STATUSES = (
    "pending",
    "running",
    "passed",
    "failed",
    "error",
    "skipped",
)

STEP_STATUSES = (
    "pending",
    "running",
    "passed",
    "failed",
    "skipped",
    "blocked_by_security",
)

DATA_CONFIDENCES = ("reliable", "synthesized", "data_failure")


class UIExecution(Base):
    """单次 UI 自动化执行批次。"""

    __tablename__ = "ui_executions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ui_test_environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'normal'"),
    )

    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped_cases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    video_path: Mapped[str | None] = mapped_column(String(500))
    trace_path: Mapped[str | None] = mapped_column(String(500))

    chat_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── 物料 / 配置快照（审计追溯用）─────────────────────────
    test_data_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    """执行时 ``TestDataResolver.serialize_for_audit`` 输出（不含 secret 明文）。"""

    config_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    """整次执行的入参快照：``loaded_set_ids / manual_overrides / llm_config_id /
    token_budget / strict_data_mode / testcase_ids`` 等。"""

    error_message: Mapped[str | None] = mapped_column(Text)
    """整体执行级别错误（典型：浏览器启动失败、严格模式拒绝、bundle 异常）。"""

    case_results: Mapped[list["UICaseResult"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="UICaseResult.sort_order",
        lazy="selectin",
    )


class UICaseResult(Base):
    """单条用例执行结果。``status`` 与 ``data_confidence`` 正交：
    ``data_confidence`` 用于业务通过率统计（自动排除"数据问题导致的失败"）。
    """

    __tablename__ = "ui_case_results"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ui_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    testcase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("testcases.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)

    duration_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    test_data_used: Mapped[list | None] = mapped_column(JSONB)
    """``[{key, source, used_in_steps: [int, ...]}, ...]``，记录"用了哪些 key"。"""

    synthesized_data: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    """AI 自造记录（来源 ``cache_synthesized`` / ``platform_synthesize_data``）。"""

    data_failures: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    """AI 上报的数据问题（``platform_mark_data_failure``）。"""

    data_confidence: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'reliable'"),
    )

    execution: Mapped[UIExecution] = relationship(back_populates="case_results")
    step_results: Mapped[list["UIStepResult"]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        order_by="UIStepResult.step_number",
        lazy="selectin",
    )


class UIStepResult(Base):
    """单步骤执行结果。tool_calls / snapshot 等大字段已脱敏（secret value 只
    留 ``"<secret used>"`` 占位）。"""

    __tablename__ = "ui_step_results"

    case_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ui_case_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_result: Mapped[str | None] = mapped_column(Text)

    tool_calls: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    """``[{name, raw_name, arguments, blocked, error, duration_ms, snapshot_chars}, ...]``。"""

    ai_reasoning: Mapped[str | None] = mapped_column(Text)
    snapshot_before: Mapped[str | None] = mapped_column(Text)
    snapshot_after: Mapped[str | None] = mapped_column(Text)

    assertion_passed: Mapped[bool | None] = mapped_column(Boolean)
    assertion_reason: Mapped[str | None] = mapped_column(Text)
    assertion_evidence: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'"),
    )
    screenshot_path: Mapped[str | None] = mapped_column(String(500))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    case_result: Mapped[UICaseResult] = relationship(back_populates="step_results")
