"""测试物料模块的 Pydantic 模式。

核心设计约束：

1. **secret 值出站即遮蔽**。``TestDataItemResponse`` 永远不携带 secret 明文；
   ``value_text`` 返回 ``None``，前端用 ``has_secret_value`` 区分"未配置" vs
   "已加密保存"。想读明文必须走专门的 ``GET /.../reveal`` 接口。

2. **value_type 对应字段白名单**：create 时允许传的 value 字段依 type 限定。
   service 层会清理"不属于 type 的字段"，schemas 层只做格式校验。

3. **与 models 层常量同步**：``VALUE_TYPE_PATTERN`` / ``SCOPE_PATTERN`` 必须
   与 ``models.VALUE_TYPES`` / ``models.SCOPES`` 保持一致，CI 在 import 阶段
   通过 ``_sanity_check_*`` 强校验。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ─── 常量（与 models.py 同步）────────────────────────────────────────

VALUE_TYPE_PATTERN = r"^(string|secret|multiline|file|random|dataset)$"
SCOPE_PATTERN = r"^(project|environment|personal)$"

# key 命名约束：以字母开头，后续字母/数字/下划线；不超过 100 字符
# 这与模板占位 ``{{key}}`` 的解析器保持一致，避免支持"诡异字符"带来的歧义。
_KEY_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")


# ─── TestDataSet Schemas ─────────────────────────────────────────────


class TestDataSetCreateRequest(BaseModel):
    __test__ = False

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(None, max_length=50)
    scope: str = Field("project", pattern=SCOPE_PATTERN)

    environment_id: uuid.UUID | None = None
    """scope=environment 时必填；其他 scope 下必须为 None（service 层校验）。"""

    owner_id: uuid.UUID | None = None
    """scope=personal 时由 service 层自动填当前用户；API 传了会被忽略。"""

    is_default: bool = False


class TestDataSetUpdateRequest(BaseModel):
    __test__ = False

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(None, max_length=50)
    is_default: bool | None = None
    # scope / environment_id / owner_id 一经创建不可改——防止"把项目级物料
    # 转成私有"之类的隐性语义变更；要改就删了重建。


class TestDataSetResponse(BaseModel):
    __test__ = False

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    category: str | None = None
    scope: str

    environment_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    is_default: bool

    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    item_count: int = 0
    """当前集合下 item 数量；list 接口聚合查询时写入。"""

    model_config = {"from_attributes": True}


class TestDataSetDetailResponse(TestDataSetResponse):
    __test__ = False

    items: list["TestDataItemResponse"] = []


# ─── TestDataItem Schemas ────────────────────────────────────────────


class TestDataItemCreateRequest(BaseModel):
    """创建非 file 物料（file 物料走 multipart 端点，不用本 schema）。"""

    __test__ = False

    key: str = Field(..., min_length=1, max_length=100)
    value_type: str = Field(..., pattern=VALUE_TYPE_PATTERN)
    description: str | None = None
    sort_order: int = Field(0, ge=0, le=10_000)

    # ── 按 type 分支的 value 字段 ──────────────────────────
    value_text: str | None = Field(
        None,
        description="string / multiline / random 使用。random 类型存的是模板，如 'phone:CN'。",
    )
    value_secret: str | None = Field(
        None,
        description="secret 类型的**明文值**；入库前被 Fernet 加密。",
    )
    value_json: Any = Field(
        None,
        description="dataset 类型的 JSON 值（通常是 list[dict]）。",
    )

    @field_validator("key")
    @classmethod
    def _check_key_format(cls, v: str) -> str:
        if not _KEY_REGEX.match(v):
            raise ValueError(
                "key 必须以字母开头，仅含字母、数字、下划线（长度 ≤ 100）",
            )
        return v


class TestDataItemUpdateRequest(BaseModel):
    """PATCH item：value_type 不可改；想换类型就删了重建。"""

    __test__ = False

    key: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    sort_order: int | None = Field(None, ge=0, le=10_000)

    value_text: str | None = None
    value_secret: str | None = None
    value_json: Any = None

    # 单独三个 clear_* flag 区分"不改"和"置空"：
    clear_value_text: bool = False
    clear_value_secret: bool = False
    clear_value_json: bool = False

    @field_validator("key")
    @classmethod
    def _check_key_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _KEY_REGEX.match(v):
            raise ValueError(
                "key 必须以字母开头，仅含字母、数字、下划线（长度 ≤ 100）",
            )
        return v


class TestDataItemResponse(BaseModel):
    __test__ = False

    id: uuid.UUID
    set_id: uuid.UUID
    key: str
    value_type: str
    description: str | None = None
    sort_order: int

    # 明文只在非 secret 类型返回；secret 永远 None
    value_text: str | None = None
    value_json: Any = None

    has_secret_value: bool = False
    """secret 类型：True=已有加密值 / False=未设置。非 secret 类型恒 False。"""

    file_path: str | None = None
    """仅对非 secret 的 file 物料暴露（相对路径，前端用于拼 download URL）。
    实际下载走 ``GET /test-data-items/{id}/file``。"""

    file_size: int | None = None
    file_mime: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestDataImportItem(BaseModel):
    """JSON 导入时的单条物料，字段与 ``TestDataItemCreateRequest`` 对齐。

    file 类型不支持从导入通道创建（文件内容无处可塞）；值由调用方显式留空则
    视为占位条目，服务端不报错。
    """

    __test__ = False

    key: str = Field(..., min_length=1, max_length=100)
    value_type: str = Field(..., pattern=VALUE_TYPE_PATTERN)
    description: str | None = None
    sort_order: int = Field(0, ge=0, le=10_000)

    value_text: str | None = None
    value_secret: str | None = None
    value_json: Any = None

    @field_validator("key")
    @classmethod
    def _check_key_format(cls, v: str) -> str:
        if not _KEY_REGEX.match(v):
            raise ValueError(
                "key 必须以字母开头，仅含字母、数字、下划线（长度 ≤ 100）",
            )
        return v


class TestDataImportRequest(BaseModel):
    """JSON 导入 body。

    ``mode`` 决定与已有 key 冲突时的行为：
    - ``skip_existing`` (默认)：遇到已有 key 直接跳过，计入 report.skipped
    - ``upsert``：遇到已有 key 按新内容覆盖对应 value 字段（保留 key / value_type）
    """

    __test__ = False

    items: list[TestDataImportItem] = Field(..., min_length=1, max_length=10_000)
    mode: str = Field("skip_existing", pattern=r"^(skip_existing|upsert)$")


class TestDataImportError(BaseModel):
    """单行导入错误详情。row 从 1 开始，对应 CSV 的数据行（不含 header）。"""

    __test__ = False

    row: int
    key: str | None = None
    message: str


class TestDataImportReport(BaseModel):
    """导入操作结果汇总。部分成功时仍 ``success`` 在外层，report 自带细节。"""

    __test__ = False

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[TestDataImportError] = []
    total: int


class TestDataSetCloneRequest(BaseModel):
    """克隆一个现有物料集为新集合。

    clone 会把 set 元数据 + 所有 items 一起复制；file 类型的物理文件也会
    复制到新 set 目录（避免共享路径被任一边删除时影响对方）。

    ``scope`` 省略时继承原 set 的 scope；但如果原是 ``personal`` 且调用者
    不是 owner，service 会强制把 clone 的 scope 转成 ``personal`` 并指向
    当前用户（语义：另存为自己的私人副本）。
    """

    __test__ = False

    new_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(None, max_length=50)
    scope: str | None = Field(None, pattern=SCOPE_PATTERN)
    environment_id: uuid.UUID | None = None
    is_default: bool = False


class TestDataSaveAsSetRequest(BaseModel):
    """把"执行弹窗的临时 overrides"另存为新物料集。

    overrides 结构与 import items 一致但 scope 默认 personal（触发人的私人 set）。
    典型流程：执行前弹窗里改了几个值 → 跑完觉得想保留 → 调这个端点落地。
    """

    __test__ = False

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(None, max_length=50)
    scope: str = Field("personal", pattern=SCOPE_PATTERN)
    environment_id: uuid.UUID | None = None
    overrides: list[TestDataImportItem] = Field(..., min_length=1, max_length=500)


class RecommendedSet(BaseModel):
    """推荐候选物料集 + 推荐原因（给前端在卡片上渲染 tag 用）。"""

    __test__ = False

    set: "TestDataSetResponse"
    reason: str
    reason_code: str
    """
    - ``env_default``：当前环境 ``default_data_set_ids`` 指向（验收新增，最优先）
    - ``project_default``：项目级 is_default=true
    - ``testcase_default``：用例 default_data_set_ids 指向
    - ``personal``：当前用户 personal scope 的集合
    - ``popular``：按条目数量 top3（无执行历史时用作启发值）
    """


class RecommendResponse(BaseModel):
    __test__ = False

    items: list[RecommendedSet]


class TestDataMergePreviewRequest(BaseModel):
    """执行配置弹窗的"合并预览"请求体（Task 9.3）。

    所有字段都是可选；空 body 也合法（结果即"个人 + 项目默认 + 环境绑定"）。

    - ``set_ids``：弹窗里勾选 / 推荐加载的物料集 id 顺序列表（loaded 层）
    - ``environment_id``：选定环境时一并加载环境绑定 + ``default_data_set_ids``
    - ``testcase_ids``：把这些用例的 ``default_data_set_ids`` 作为附加 loaded
      并入。注意预览阶段没有"用例级覆盖"概念，因此把它们与 ``set_ids``
      平铺在 loaded 层，反映"实际跑这些用例时都会加载哪些 set"
    - ``manual_overrides``：弹窗里临时改写的键值对（最高优先级）
    """

    __test__ = False

    set_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    environment_id: uuid.UUID | None = None
    testcase_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    manual_overrides: dict[str, Any] = Field(default_factory=dict)


class TestDataMergeSource(BaseModel):
    """合并预览中"该 key 来自哪个物料集 / 哪一层"的单条溯源记录。

    设计：
    - 同一个 key 在多个物料集中重复时，``sources`` 会展开成多条；按合并
      顺序追加（personal → project → environment_bind → environment_default
      → loaded → testcase_default → manual_override），数组里**最后一条 =
      合并胜出值**，其它条 ``overridden=True``。
    - secret 永远只输出 ``●●●●`` / ``has_secret_value=True``，不发明文。
    - manual override 没有 ``set_id``，``scope='manual'``，便于前端识别"用户
      在弹窗里手工改写过"。

    新增动机：用户验收反馈"多个物料集都有 key=username，合并明细只展示一条
    username"——以前 ``MergedItem`` 只有最终生效值，多集同名时无法在弹窗里看
    到被覆盖的候选值，没法判断要不要调整加载顺序。
    """

    __test__ = False

    set_id: uuid.UUID | None = None  # manual override 时为 None
    set_name: str
    scope: str  # personal / project / environment / loaded / testcase / manual
    display_value: str
    has_secret_value: bool = False
    file_name: str | None = None
    overridden: bool = False
    """``True`` = 该来源被后续层覆盖；``False`` = 当前生效值。"""


class TestDataMergedItem(BaseModel):
    """合并预览的单条物料（safe-by-default，secret 不暴露明文）。"""

    __test__ = False

    key: str
    value_type: str
    description: str | None = None
    display_value: str
    """前端展示用：secret → ``●●●●``、file → 文件名、其他 → 截断后的明文。"""
    has_secret_value: bool = False
    file_name: str | None = None
    synthetic_source: str | None = None
    """仅当合并阶段命中过自造缓存才会填（执行时由 platform_synthesize_data 写入）。"""
    sources: list[TestDataMergeSource] = Field(default_factory=list)
    """该 key 在合并链中出现过的所有来源；按合并顺序追加，最后一条 = 胜出值。

    空数组通常意味着是 ad-hoc / 自造数据，没有真实物料集来源（比如 manual
    override 命中前合并 dict 里没有该 key 时 ``_apply_manual`` 会 ``adhoc()`` 添加）。"""


class TestDataMergePreviewResponse(BaseModel):
    __test__ = False

    items: list[TestDataMergedItem]


class TestDataMissingCheckRequest(TestDataMergePreviewRequest):
    """missing-check 体复用合并预览请求字段，再加用例步骤来源。"""

    __test__ = False


class TestDataMissingStepRef(BaseModel):
    __test__ = False

    testcase_id: uuid.UUID
    step_number: int
    where: str  # action / expected


class TestDataMissingAlert(BaseModel):
    __test__ = False

    key: str
    detected_in_steps: list[TestDataMissingStepRef] = []
    will_synthesize: bool = True


class TestDataMissingCheckResponse(BaseModel):
    __test__ = False

    missing_keys: list[str]
    will_synthesize: bool = True
    details: list[TestDataMissingAlert] = []


class TestDataItemRevealResponse(BaseModel):
    """``GET /test-data-items/{id}/reveal`` 的返回。

    同时暴露明文 text 和 secret，方便管理员在 UI 上直接看当前值（secret 场景
    常见需求：修改前要先确认当前密码）。权限控制由 router 层卡死。
    """

    __test__ = False

    id: uuid.UUID
    key: str
    value_type: str
    value_text: str | None = None
    value_secret: str | None = None
    value_json: Any = None


# ─── Forward ref ─────────────────────────────────────────────────────


TestDataSetDetailResponse.model_rebuild()
RecommendedSet.model_rebuild()


# ─── 一致性 sanity check ─────────────────────────────────────────────
#
# 当有人动了 models.VALUE_TYPES / SCOPES 但忘了同步这里的 Pattern 时，
# import 阶段立刻炸掉，避免运行时沉默失败。


def _sanity_check_patterns_match_model_constants() -> None:
    from app.modules.test_data.models import SCOPES, VALUE_TYPES

    vtype_re = re.compile(VALUE_TYPE_PATTERN)
    for t in VALUE_TYPES:
        if not vtype_re.fullmatch(t):
            raise RuntimeError(
                f"schemas.VALUE_TYPE_PATTERN missing {t!r} from models.VALUE_TYPES",
            )

    scope_re = re.compile(SCOPE_PATTERN)
    for s in SCOPES:
        if not scope_re.fullmatch(s):
            raise RuntimeError(
                f"schemas.SCOPE_PATTERN missing {s!r} from models.SCOPES",
            )


_sanity_check_patterns_match_model_constants()
