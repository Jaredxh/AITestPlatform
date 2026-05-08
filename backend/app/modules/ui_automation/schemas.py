"""UI 自动化模块的 Pydantic 模式。

请求 / 响应分得很细，刻意让"敏感凭据进 / 不出"成为类型层面的约束：
- ``PreconditionTemplateCreateRequest.credentials``：dict[str, Any]，明文进
- ``PreconditionTemplateResponse.has_credentials``：bool，只告知有没有，不返回明文
- 想读明文走另外的 reveal endpoint（本 task 暂不实现，留给真有需求时加）

字段 pattern 与 ``models.PRECONDITION_TYPES`` 同步；改集合时两边都要动。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

# 与 ``models.PRECONDITION_TYPES`` 对齐；pydantic 不能直接复用 tuple 做 Pattern，
# 所以这里维护一份与 model 同步的 regex（CI 中由 test_schemas 验证一致性）。
PRECONDITION_TYPE_PATTERN = r"^(state_inject|ai_login|scripted_steps|cookie_inject|http_login)$"


# ─────────────────── TestEnvironment ───────────────────


class TestEnvironmentCreateRequest(BaseModel):
    """创建环境。``allowed_hosts`` 留空时 service 会自动从 base_url 提取 host 填入。"""

    # 类名以 Test 开头会被 pytest 误判为测试类；显式标记跳过收集。
    __test__ = False

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None

    base_url: AnyHttpUrl
    """完整 URL，含 scheme（http/https）。pydantic 会校验格式。"""

    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="放行的 host 列表；留空时从 base_url 自动提取",
    )
    token_budget: int = Field(25_000, ge=1_000, le=10_000_000)
    enable_browser_evaluate: bool = False
    session_name: str | None = Field(None, max_length=100)
    default_data_set_ids: list[uuid.UUID] = Field(default_factory=list)

    headless: bool = True
    viewport_width: int = Field(1280, ge=320, le=4096)
    viewport_height: int = Field(800, ge=240, le=4096)

    @field_validator("allowed_hosts")
    @classmethod
    def _strip_hosts(cls, v: list[str]) -> list[str]:
        return [h.strip().lower() for h in v if h and h.strip()]


class TestEnvironmentUpdateRequest(BaseModel):
    """所有字段可选；None = 不改。"""

    __test__ = False

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    base_url: AnyHttpUrl | None = None
    allowed_hosts: list[str] | None = None
    token_budget: int | None = Field(None, ge=1_000, le=10_000_000)
    enable_browser_evaluate: bool | None = None
    session_name: str | None = Field(None, max_length=100)
    default_data_set_ids: list[uuid.UUID] | None = None

    headless: bool | None = None
    viewport_width: int | None = Field(None, ge=320, le=4096)
    viewport_height: int | None = Field(None, ge=240, le=4096)

    @field_validator("allowed_hosts")
    @classmethod
    def _strip_hosts(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return [h.strip().lower() for h in v if h and h.strip()]


class TestEnvironmentResponse(BaseModel):
    """环境列表 / 单条返回的基础形状（不含 preconditions）。"""

    __test__ = False

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    base_url: str
    allowed_hosts: list[str]
    token_budget: int
    enable_browser_evaluate: bool
    session_name: str | None
    state_saved_at: datetime | None
    default_data_set_ids: list[str]
    headless: bool
    viewport_width: int
    viewport_height: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestEnvironmentDetailResponse(TestEnvironmentResponse):
    __test__ = False

    preconditions: list["PreconditionTemplateResponse"] = []


# ─────────────────── PreconditionTemplate ───────────────────


class PreconditionTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., pattern=PRECONDITION_TYPE_PATTERN)
    description: str | None = None

    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = Field(
        None,
        description=(
            "敏感凭据（username/password/cookie/token 等）。写库前会用 Fernet 加密；"
            "查询时不会原样返回，只通过 has_credentials=true 暴露存在性。"
        ),
    )
    order_index: int = Field(0, ge=0, le=10_000)
    enabled: bool = True


class PreconditionTemplateUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    type: str | None = Field(None, pattern=PRECONDITION_TYPE_PATTERN)
    description: str | None = None
    config: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = Field(
        None,
        description="传 None = 不改；传 {} = 清空已有凭据；传非空 dict = 覆盖加密。",
    )
    clear_credentials: bool = Field(
        False, description="True 时显式清空 credentials_encrypted（与 credentials=None 区分）",
    )
    order_index: int | None = Field(None, ge=0, le=10_000)
    enabled: bool | None = None


class PreconditionTemplateResponse(BaseModel):
    id: uuid.UUID
    environment_id: uuid.UUID
    name: str
    type: str
    description: str | None = None
    config: dict[str, Any]
    has_credentials: bool
    order_index: int
    enabled: bool
    state_saved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# Resolve forward ref（TestEnvironmentDetailResponse 引用了 PreconditionTemplateResponse）
TestEnvironmentDetailResponse.model_rebuild()


# ─────────────────── State 治理 ───────────────────


class ClearStateResponse(BaseModel):
    """``POST /environments/{id}/clear-state`` 的返回。"""

    environment_id: uuid.UUID
    state_file_existed: bool
    state_file_removed: bool


# ─────────────────── PreconditionTest（Task 8.2 试跑端点）────────────


class TestPreconditionRequest(BaseModel):
    """``POST /preconditions/{id}/test`` 的可选 body。

    本 task 暴露的"试跑"端点，故意保留极简：执行结果完全由模板自身决定，
    用户能调整的只有"要不要把成功后的 storage_state 持久化到磁盘"。

    默认 ``persist_state=False`` —— 试跑不污染正式环境的 state 文件，
    避免"我只是想看看登录步骤对不对，结果改写了真在跑的 execution 用的 state"。
    """

    __test__ = False

    persist_state: bool = Field(
        False,
        description="True = 成功后写 storage_state 到环境配置的目标位置；False（默认）= 只跑不存",
    )
    timeout_seconds: float = Field(
        # 默认 300s（5 分钟）——AI 驱动登录的瓶颈不是浏览器交互而是 LLM
        # inference：火山引擎 ark / GLM 5.1 这类模型每轮 ``chat completions``
        # 实测 30-60s，10 步 ≈ 5-10 分钟。180s 在中等慢度模型下都吃紧；
        # 600s 上限保留给最慢链路（带验证码 / 多步选项 / 弱网环境）。
        300.0, ge=5.0, le=600.0,
        description=(
            "单条模板硬超时（秒）；超过即返回 timeout 结果。"
            "AI 登录建议 ≥300s（取决于 LLM 速度），cookie/state/script 类型可降到 30s。"
        ),
    )


class TestPreconditionResponse(BaseModel):
    """``POST /preconditions/{id}/test`` 的返回。

    字段直接镜像 ``PreconditionResult`` dataclass，方便前端展示一条详细的
    "试跑日志卡"。``screenshot_base64`` 是 PNG，可直接 ``data:image/png;base64,...``
    嵌前端 ``<img>``。
    """

    __test__ = False

    template_id: uuid.UUID
    template_name: str
    type: str
    success: bool
    elapsed_ms: int

    error: str | None = None
    error_kind: str | None = None
    screenshot_base64: str | None = None

    state_was_loaded: bool = False
    state_was_stale: bool = False
    state_was_saved: bool = False
    state_saved_path: str | None = None
    fell_back_to: str | None = None

    logs: list[str] = []


# ─── 内部一致性 sanity check（fail-fast，不在运行时跑）────────────────
# 当有人改了 PRECONDITION_TYPE_PATTERN 但忘了同步 models.PRECONDITION_TYPES 时，
# 立刻在 import 阶段炸掉，比上线后才发现"为什么 ai_login 不能创建"友好得多。

def _sanity_check_pattern_matches_model_constants() -> None:
    from app.modules.ui_automation.models import PRECONDITION_TYPES
    pattern = re.compile(PRECONDITION_TYPE_PATTERN)
    for t in PRECONDITION_TYPES:
        if not pattern.fullmatch(t):
            raise RuntimeError(
                f"schemas.PRECONDITION_TYPE_PATTERN missing '{t}' from models.PRECONDITION_TYPES — "
                "改其中一个时务必同步另一个"
            )


_sanity_check_pattern_matches_model_constants()


# ─────────────────── Task 9.6 - 执行 API ────────────────────────────


# 与 ``models.EXECUTION_MODES`` 同步；改这里时务必同步两边。
EXECUTION_MODE_PATTERN = r"^(normal|debug)$"


class ExecutionCreateRequest(BaseModel):
    """``POST /projects/{id}/ui-executions`` 的请求体。

    设计要点（PHASE2 §2.5.1 单弹窗折叠式）：
    - **零必填** 除 ``testcase_ids`` 外全部可选；缺料只产生 SSE 警告，不阻断
    - ``environment_id`` 缺省时由 service 取项目最新环境（兼容"项目只有 1 套环境"
      的常见情况）；都没有则报 400，强制用户去配置
    - ``mode`` 默认 ``normal``；``debug`` 走单步暂停（Task 9.7 实现）
    - ``token_budget`` 覆盖环境默认；为 None 时复用 environment.token_budget
    - ``strict_data_mode`` 严格模式：preflight 发现缺 key 直接拒绝，与 v3.0.1
      "缺料只警告"的默认相反，给"不接受 AI 自造数据"的人留口子
    """

    __test__ = False

    testcase_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=200,
        description=(
            "本次执行的用例 ID 列表，按本数组顺序跑；与 ``plan_id`` 互斥——"
            "传 ``plan_id`` 时由后端按缓存 plan 还原 testcase_ids，前端不必传。"
        ),
    )
    environment_id: uuid.UUID | None = Field(
        None,
        description="测试环境；不填时 service 取项目最新环境",
    )
    mode: str = Field("normal", pattern=EXECUTION_MODE_PATTERN)
    llm_config_id: uuid.UUID | None = None
    loaded_set_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="本次执行临时加载的物料集；与环境/项目/用例级合并（详见 §3.6）",
    )
    manual_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="本次临时覆盖的 key/value（最高优先级，可暴露 secret 仅本次）",
    )
    token_budget: int | None = Field(
        None, ge=1_000, le=10_000_000,
        description="本次覆盖环境 token 预算；None=用环境默认",
    )
    strict_data_mode: bool = Field(
        False,
        description="True = preflight 发现缺 key 直接拒绝；False = 缺料 AI 自造兜底",
    )
    chat_message_id: uuid.UUID | None = Field(
        None,
        description="若由 AI 对话触发，传 assistant_message_id 做关联（Task 9.6）",
    )
    module_entry_overrides: dict[uuid.UUID, str] = Field(
        default_factory=dict,
        description=(
            "按 module_id 临时覆盖 module.entry_path（可选）。值可以是相对路径"
            "（``/admin/users``）或完整 URL（``https://other.example.com/x``）。"
            "传空串等同于'本次跑该模块时不带 entry_path'，让 AI 直接用 base_url。"
            "未列出的模块沿用 module.entry_path（即数据库里配的）。"
        ),
    )
    # ─── Phase 13 / Task 13.3 — chat 派发 / plan_id 还原 ────────────────────
    plan_id: uuid.UUID | None = Field(
        None,
        description=(
            "Phase 13：由 ``system__ui_automation__propose_execution_plan`` 缓存"
            "的 plan id；传此字段时 service 用 plan 还原 testcase_ids / "
            "environment_id / llm_config_id，无需前端再送 testcase_ids。"
            "TTL 10 分钟，过期返回 410。"
        ),
    )
    source: str | None = Field(
        None,
        pattern=r"^(catalog|chat|adhoc)$",
        description=(
            "Phase 13：派发来源；``catalog`` = ExecuteDialog，``chat`` = AI 对话"
            "ConfirmationCard，``adhoc`` = M2 即席执行。落 ``ui_executions.source``"
            "用于历史筛选 + 防 adhoc 污染概览统计。不传走默认 ``catalog``。"
        ),
    )
    triggered_chat_session_id: uuid.UUID | None = Field(
        None,
        description=(
            "Phase 13：由 chat 派发时的会话 id；执行完成时 SystemEventService 据"
            "此把 ``execution_event`` 系统消息回流到该会话末尾。"
        ),
    )


class PreflightModulesRequest(BaseModel):
    """``POST /projects/{id}/ui-executions/preflight-modules`` 的请求体。

    用于执行配置弹窗在用户提交前预览：
    - 本批次涉及哪些模块（distinct 去重）
    - 每个模块当前的 entry_path（DB 里持久存的）
    - 每个模块对应的"无 module 用例数"——> 提示用户"这些用例没归模块，
      没法走 entry_path 流程"
    """

    __test__ = False

    testcase_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)


class PreflightModuleItem(BaseModel):
    """一个被涉及的模块。``module_id=None`` 是兜底行——表示"还有 N 条用例没归模块"。"""

    __test__ = False

    module_id: uuid.UUID | None = None
    module_name: str | None = None
    entry_path: str | None = None
    case_count: int


class PreflightModulesResponse(BaseModel):
    __test__ = False

    items: list[PreflightModuleItem]


class ExecutionListItem(BaseModel):
    """列表项：精简到表格能渲染的字段，详细数据走 ``GET /{id}``。"""

    __test__ = False

    id: uuid.UUID
    project_id: uuid.UUID
    environment_id: uuid.UUID | None = None
    status: str
    mode: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_cases: int
    # 数据可信度三态计数（v3.0.1 新增）。前端"业务视图"通过率需要把
    # data_failure_cases 从分母中剔除；省略这三个字段时按 0 处理。
    reliable_cases: int = 0
    synthesized_cases: int = 0
    data_failure_cases: int = 0
    duration_ms: int | None = None
    tokens_total: int = 0
    has_video: bool = False
    has_trace: bool = False
    triggered_by: uuid.UUID | None = None
    chat_message_id: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExecutionStepResponse(BaseModel):
    __test__ = False

    id: uuid.UUID
    case_result_id: uuid.UUID
    step_number: int
    description: str
    expected_result: str | None = None
    tool_calls: list[dict[str, Any]] = []
    ai_reasoning: str | None = None
    snapshot_before: str | None = None
    snapshot_after: str | None = None
    assertion_passed: bool | None = None
    assertion_reason: str | None = None
    assertion_evidence: str | None = None
    status: str
    screenshot_path: str | None = None
    # Web 可访问的 URL（典型形如 ``/uploads/ui_artifacts/<exec_id>/steps/xxx.png``）。
    # 由 nginx 直接出静态资源，无需 token 鉴权 —— 因此前端 ``<img src>`` 可以直
    # 接用，而不必走需要 Bearer token 的 ``/api/ui-executions/steps/{id}/screenshot``
    # 端点（``<img>`` 标签发请求时不会带 Authorization header）。
    screenshot_url: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    tokens_used: int = 0
    duration_ms: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ExecutionCaseResponse(BaseModel):
    __test__ = False

    id: uuid.UUID
    execution_id: uuid.UUID
    testcase_id: uuid.UUID | None = None
    # 用例当时的业务编号（``Testcase.case_no``，前端通常渲染为 ``TC-0117``
    # 格式给人看）；UUID 给后端用作稳定外键，``case_no`` 给人看。如果用例
    # 已删除则为 None。
    testcase_no: int | None = None
    # 用例当时的标题 / 所属模块；从 testcases 表 join 出来（用例可能事后被
    # 改名 / 删除，因此前端拿到的就是"现在的最新值"，这一点不算严格的快照
    # 语义，但对"测试报告"的可读性收益最大；如果用例被删，title=None）
    testcase_title: str | None = None
    testcase_module_id: uuid.UUID | None = None
    testcase_module_name: str | None = None
    status: str
    error_message: str | None = None
    ai_summary: str | None = None
    duration_ms: int | None = None
    tokens_used: int = 0
    sort_order: int = 0
    test_data_used: list[dict[str, Any]] | None = None
    synthesized_data: list[dict[str, Any]] = []
    data_failures: list[dict[str, Any]] = []
    data_confidence: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[ExecutionStepResponse] = []

    model_config = {"from_attributes": True}


class ExecutionDetailResponse(BaseModel):
    """``GET /ui-executions/{id}`` 的完整响应：执行+用例+步骤一次取齐。"""

    __test__ = False

    id: uuid.UUID
    project_id: uuid.UUID
    environment_id: uuid.UUID | None = None
    status: str
    mode: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_cases: int
    duration_ms: int | None = None
    tokens_total: int = 0
    video_path: str | None = None
    trace_path: str | None = None
    # video_url / trace_url 是 **nginx 静态路径**（典型 ``/uploads/ui_artifacts/<exec_id>/video/xxx.webm``），
    # 给 ``<video src>`` / ``<a href download>`` 直接用 —— HTML media 标签的请
    # 求**不会自动带** Authorization header，必须走静态路径绕开后端鉴权。
    # ``video_path`` / ``trace_path`` 保留是为了运维 / 排错（直接看后端绝对
    # 路径），但前端 UI 不应该用它们渲染媒体。详见 ``_artifact_path_to_url``。
    video_url: str | None = None
    trace_url: str | None = None
    has_video: bool = False
    has_trace: bool = False
    chat_message_id: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    triggered_by: uuid.UUID | None = None
    test_data_snapshot: dict[str, Any] | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    # 本次执行实际生效的 token 预算上限：
    # ``token_budget_override`` 优先 > environment.token_budget > 全局默认
    # 前端监控页用它做进度条最大值，不再写死 50_000
    effective_token_budget: int = 50_000
    created_at: datetime
    updated_at: datetime
    case_results: list[ExecutionCaseResponse] = []

    model_config = {"from_attributes": True}


class ExecutionStopResponse(BaseModel):
    """``POST /ui-executions/{id}/stop`` 返回。

    幂等：如果 execution 已是终态（completed/failed/aborted_budget/stopped），
    ``already_terminal=True`` 且 ``status`` 字段保留原状态——不报错。
    """

    __test__ = False

    execution_id: uuid.UUID
    status: str
    already_terminal: bool


class ExecutionContinueResponse(BaseModel):
    """``POST /ui-executions/{id}/continue`` 返回（Task 9.7）。

    幂等：execution 不在 debug 暂停 / 已结束 → ``signal_delivered=False``，
    端点不报错（前端可用作"继续按钮总是可点"的视觉一致性）。
    """

    __test__ = False

    execution_id: uuid.UUID
    signal_delivered: bool
    status: str  # 当前 execution.status，便于前端立即更新 UI


class ExecutionRetryRequest(BaseModel):
    """``POST /ui-executions/{id}/retry-failed`` 的可选 body。

    默认行为：从原 execution 抽出 status in (failed/error/skipped) 的用例 + 复
    用原 config_snapshot（loaded_set_ids / manual_overrides / llm_config_id /
    token_budget / strict_data_mode），开一个新 execution 跑。如果用户想换环境/
    覆盖配置，可传字段做局部修改。
    """

    __test__ = False

    environment_id: uuid.UUID | None = None
    llm_config_id: uuid.UUID | None = None
    token_budget: int | None = Field(None, ge=1_000, le=10_000_000)
    strict_data_mode: bool | None = None
    extra_loaded_set_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="在原 loaded_set_ids 基础上追加的物料集",
    )
    extra_manual_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="在原 manual_overrides 基础上追加/覆盖的 key/value",
    )
