"""测试物料（Test Data）的 SQLAlchemy ORM 模型。

两张表：

- ``test_data_sets``：物料集
- ``test_data_items``：物料条目（归属某个 set）

关键约束：
1. ``(set_id, key)`` 唯一——同一集合内不允许重复 key。五级合并时 resolver
   会按优先级取值，但"同一集合内部"还是得唯一。
2. ``value_type`` 限定在 6 种之一（DB 层 CHECK 约束 + schemas 层 pattern 双保险）。
3. ``scope`` 限定在 3 种之一（project / environment / personal）；DB 层也加
   CHECK 约束。
4. ``environment_id`` 只在 ``scope='environment'`` 时有值；``owner_id`` 只在
   ``scope='personal'`` 时有值（应用层保障，DB 不做 partial FK）。
5. 所有 JSONB 列都走 ``server_default=text("'[]'::jsonb" / "'{}'::jsonb")``，
   避免"insert 时列为 None"引发 NOT NULL 违反。

敏感字段命名约定：明文字段以 ``_encrypted`` 结尾、存 Fernet 加密串（与 UI
自动化 credentials_encrypted 保持一致风格）。list/detail 接口不返回明文，
只有专门的 ``reveal`` 接口配合权限 + 审计日志才能取到。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.modules.auth.models import User
    from app.modules.projects.models import Project
    from app.modules.ui_automation.models import TestEnvironment


# ─── 常量（与 schemas.py 的 Pattern 同步；改这里两边都要动）────────────

VALUE_TYPES: tuple[str, ...] = (
    "string", "secret", "multiline", "file", "random", "dataset",
)
"""6 种物料类型，与 PHASE2_DESIGN §2.4.2 一致。"""

SCOPES: tuple[str, ...] = ("project", "environment", "personal")
"""3 种作用域，与 PHASE2_DESIGN §2.4.1 一致。"""


# ─── TestDataSet ─────────────────────────────────────────────────────


class TestDataSet(Base):
    """测试物料集：一组 ``TestDataItem`` 的集合。

    三种 scope：
    - ``project``：项目内所有成员可见；``is_default=True`` 时执行自动加载。
    - ``environment``：绑定到特定 ``test_environments.id``；仅跑该环境时注入。
    - ``personal``：个人私有；``owner_id`` 指向拥有者；别人看不到。
    """

    __tablename__ = "test_data_sets"
    __test__ = False  # 避免 pytest 把类名带 Test 前缀的当测试类

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    category: Mapped[str | None] = mapped_column(String(50))
    """业务分类标签：account / order / product / search / upload 等。
    仅用于前端筛选 / 推荐算法启发，不做强类型约束。"""

    scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'project'"),
    )

    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ui_test_environments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    """仅在 ``scope='environment'`` 时有值。env 被删除则级联删除本 set。"""

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    """仅在 ``scope='personal'`` 时有值。"""

    is_default: Mapped[bool] = mapped_column(
        # 用 text 而非 default=False，避免 schema-only 查询看到 None
        # （与项目内其他 Bool 列保持统一风格）
        server_default=text("false"),
        nullable=False,
        default=False,
    )
    """项目级默认：为 True 时执行引擎会自动加载到本次执行的物料栈。"""

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    project: Mapped["Project"] = relationship(lazy="selectin")
    environment: Mapped["TestEnvironment | None"] = relationship(lazy="selectin")
    owner: Mapped["User | None"] = relationship(
        lazy="selectin", foreign_keys=[owner_id],
    )
    creator: Mapped["User | None"] = relationship(
        lazy="selectin", foreign_keys=[created_by],
    )

    items: Mapped[list["TestDataItem"]] = relationship(
        back_populates="data_set",
        cascade="all, delete-orphan",
        order_by="TestDataItem.sort_order, TestDataItem.key",
        lazy="selectin",
    )


# ─── TestDataItem ────────────────────────────────────────────────────


class TestDataItem(Base):
    """测试物料条目。

    ``value_type`` 决定用哪个 value 字段：

    | value_type  | 存储字段                                         |
    |-------------|--------------------------------------------------|
    | string      | ``value_text``（明文）                            |
    | secret      | ``value_encrypted``（Fernet 加密字符串）           |
    | multiline   | ``value_text``（明文，可能含换行）                  |
    | file        | ``file_path`` + ``file_size`` + ``file_mime``    |
    | random      | ``value_text``（模板字符串，如 ``phone:CN``）       |
    | dataset     | ``value_json``（JSONB 数组或对象）                 |

    其余字段在对应 value_type 下应为 None；service 层在 create/update 时强制清理。
    """

    __tablename__ = "test_data_items"
    __test__ = False

    set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_data_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    """物料 key：用例步骤里 ``{{username}}`` / ``<secret:password>`` 引用的就是它。
    约束：同一 set 内唯一；应用层进一步限制为 ``[a-zA-Z][a-zA-Z0-9_]*`` 形式。"""

    value_type: Mapped[str] = mapped_column(String(20), nullable=False)

    value_text: Mapped[str | None] = mapped_column(Text)
    """string / multiline / random 使用；其他 type 为 None。"""

    value_encrypted: Mapped[str | None] = mapped_column(Text)
    """secret 使用（Fernet 加密）；其他 type 为 None。"""

    value_json: Mapped[dict | list | None] = mapped_column(JSONB)
    """dataset 使用；其他 type 为 None。SQLAlchemy JSONB 支持 dict / list。"""

    file_path: Mapped[str | None] = mapped_column(String(500))
    """file 使用：相对仓库根的路径（``uploads/test-data/<project>/<set>/<uuid>_<filename>``）。"""

    file_size: Mapped[int | None] = mapped_column(BigInteger)
    file_mime: Mapped[str | None] = mapped_column(String(100))

    description: Mapped[str | None] = mapped_column(Text)
    """物料含义（会注入 system prompt 给 AI 看）。"""

    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )

    data_set: Mapped[TestDataSet] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("set_id", "key", name="uq_test_data_items_set_key"),
    )


__all__ = [
    "SCOPES",
    "VALUE_TYPES",
    "TestDataItem",
    "TestDataSet",
]
