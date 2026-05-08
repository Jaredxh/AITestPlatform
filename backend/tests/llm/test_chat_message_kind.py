"""Phase 13 / Task 13.0 — ChatMessage.kind 默认值与枚举常量。

DoD：``chat_messages.kind`` 字段加完后，二期老消息 select 不报错；不显式
传值时 ORM 行的 ``kind`` 默认为 ``'normal'``。
"""

from __future__ import annotations

import uuid

from app.modules.llm.models import CHAT_MESSAGE_KINDS, ChatMessage


def test_chat_message_kind_column_has_normal_default() -> None:
    """``kind`` 列同时配置 Python ``default`` 与 SQL ``server_default`` =
    ``'normal'``：前者覆盖通过 ORM 创建未传 kind 时的 INSERT 路径，后者覆盖
    历史行 / 直接 SQL INSERT 路径。任何一侧丢失都会让二期老消息 select 失败。
    """
    col = ChatMessage.__table__.c.kind
    # SQLAlchemy 在 flush 阶段填 ``default``；构造实例时尚未触发，因此这里
    # 直接断言 column 元数据里挂了正确默认值。
    assert col.default is not None
    assert col.default.arg == "normal"
    assert col.server_default is not None
    # server_default 是 Text("'normal'") 实例
    assert "normal" in str(col.server_default.arg)
    assert col.nullable is False


def test_chat_message_kind_explicit_value_persisted() -> None:
    """显式传 kind 时构造实例可立即读到该值（ORM 标准行为）。"""
    msg = ChatMessage(
        session_id=uuid.uuid4(),
        role="assistant",
        content="task done",
        kind="execution_event",
    )
    assert msg.kind == "execution_event"


def test_chat_message_kinds_enum_completeness() -> None:
    expected = {"normal", "skill_card", "task_badge", "execution_event"}
    assert set(CHAT_MESSAGE_KINDS) == expected
