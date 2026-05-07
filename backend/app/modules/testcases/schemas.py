import uuid
from datetime import datetime

from pydantic import BaseModel, Field, computed_field


def format_case_display_id(case_no: int | None) -> str:
    """把 project 内递增的 case_no 渲染成对外编号，例如 ``TC-0001``。

    case_no 为 0 / None 时（历史数据未回填或异常情况），降级为 ``TC-?``，
    避免前端拿到空串导致表格错位。
    """
    if not case_no or case_no <= 0:
        return "TC-?"
    return f"TC-{case_no:04d}"


# ── 模块树 ──

class ModuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    order_index: int = 0
    # 模块入口路径（可选）。配合 ``TestEnvironment.base_url`` 使用：
    # 执行时实际目标 URL = ``base_url + entry_path``。详细语义见 model。
    entry_path: str | None = Field(
        None,
        max_length=500,
        description=(
            "模块入口路径（可选）。例：``/admin/users``、``/dashboard/stats``；"
            "也可以填完整 URL 跨子域。留空时由用例步骤自然语言决定目标地址。"
        ),
    )


class ModuleUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    order_index: int | None = None
    # 注意：``entry_path`` 的更新语义是"显式 set"——前端**只在用户在入口
    # 路径表单里改动后**才传，留空字符串表示"清空"。如果整个字段缺席（
    # ``model_dump(exclude_unset=True)`` 看不到），就保留原值不动。
    entry_path: str | None = Field(None, max_length=500)


class ModuleResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    order_index: int
    entry_path: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ModuleTreeNode(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    order_index: int
    entry_path: str | None = None
    case_count: int = 0
    children: list["ModuleTreeNode"] = []

    model_config = {"from_attributes": True}


# ── 用例步骤 ──

class StepRequest(BaseModel):
    step_number: int = Field(..., ge=1)
    action: str = Field(..., min_length=1)
    expected_result: str | None = None


class StepResponse(BaseModel):
    id: uuid.UUID
    testcase_id: uuid.UUID
    step_number: int
    action: str
    expected_result: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── 测试用例 ──

class TestcaseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    module_id: uuid.UUID | None = None
    precondition: str | None = None
    priority: str = Field("medium", pattern=r"^(high|medium|low)$")
    steps: list[StepRequest] = []
    default_data_set_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description=(
            "执行该用例时默认加载的物料集 id（Task 8.5 落地，Task 9.1 消费）。"
            "顺序即优先级；相同 key 时后面的覆盖前面的。"
        ),
    )


class TestcaseUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    module_id: uuid.UUID | None = None
    precondition: str | None = None
    priority: str | None = Field(None, pattern=r"^(high|medium|low)$")
    status: str | None = Field(None, pattern=r"^(active|deprecated|draft)$")
    exec_result: str | None = Field(
        None, pattern=r"^(not_run|passed|failed|blocked)$"
    )
    steps: list[StepRequest] | None = None
    default_data_set_ids: list[uuid.UUID] | None = None


class TestcaseResponse(BaseModel):
    id: uuid.UUID
    case_no: int = 0
    project_id: uuid.UUID
    module_id: uuid.UUID | None
    module_name: str | None = None
    title: str
    precondition: str | None
    priority: str
    status: str
    source: str
    exec_result: str = "not_run"
    created_by: uuid.UUID
    creator_name: str | None = None
    steps: list[StepResponse] = []
    default_data_set_ids: list[uuid.UUID] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def display_id(self) -> str:
        return format_case_display_id(self.case_no)


class TestcaseListItem(BaseModel):
    id: uuid.UUID
    case_no: int = 0
    module_id: uuid.UUID | None
    module_name: str | None = None
    title: str
    priority: str
    status: str
    source: str
    exec_result: str = "not_run"
    creator_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def display_id(self) -> str:
        return format_case_display_id(self.case_no)


class TestcaseListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    module_id: uuid.UUID | None = None
    priority: str | None = None
    status: str | None = None
    source: str | None = None
    exec_result: str | None = None
    search: str | None = None


# ── AI 生成 ──

class GenerateRequest(BaseModel):
    document_id: uuid.UUID
    module_id: uuid.UUID | None = None
    llm_config_id: uuid.UUID | None = None


class GeneratedTestcase(BaseModel):
    title: str
    precondition: str | None = None
    priority: str = "medium"
    steps: list[StepRequest] = []


class BatchAcceptRequest(BaseModel):
    batch_id: uuid.UUID
    testcases: list[GeneratedTestcase]
    module_id: uuid.UUID | None = None


class BatchAcceptResponse(BaseModel):
    accepted_count: int
    batch_id: uuid.UUID


class GenerationBatchResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID | None
    module_id: uuid.UUID | None = None
    model_used: str | None
    status: str
    generated_count: int
    accepted_count: int
    generation_time_ms: int | None
    created_at: datetime
    document_name: str | None = None
    module_name: str | None = None
    testcases: list[dict] = []

    model_config = {"from_attributes": True}


# ── Excel 导入 / 导出 ──


class TestcaseImportError(BaseModel):
    """单行导入错误。row 是 Excel 行号（含表头算 1，所以数据从 2 开始）。"""

    row: int
    message: str
    title: str | None = None


class TestcaseImportReport(BaseModel):
    """批量导入回执。

    HTTP 永远是 200（除非整体结构错），由前端按 ``errors`` 长度决定提示口径。
    ``created`` / ``updated`` / ``skipped`` 各自累计；行级错误聚合在 ``errors``。
    """

    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    created_modules: list[str] = Field(
        default_factory=list,
        description="导入过程中按路径自动创建的模块路径（``a/b/c``）。",
    )
    errors: list[TestcaseImportError] = Field(default_factory=list)
