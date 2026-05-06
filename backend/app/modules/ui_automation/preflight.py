"""执行前缺料告警（非阻断）。

设计 §3.6.6：扫描所有 ``step.action`` / ``step.expected_result`` 中的
``{{key}}`` 占位，与 ``TestDataResolver.data`` 已合并键集做差集，得到
本次执行无显式物料供给的 key 列表。

此告警**不会**阻断执行：上层 ExecutionEngine 拿到结果后通过 SSE
``missing_data_warning`` 事件提示前端，AI 在跑步骤时仍可调用
``platform_synthesize_data`` 自造数据继续。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.modules.ui_automation.test_data_resolver import TestDataResolver


_VAR_PATTERN = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


@runtime_checkable
class _StepLike(Protocol):
    step_number: int
    action: str | None
    expected_result: str | None


@runtime_checkable
class _TestcaseLike(Protocol):
    id: uuid.UUID
    steps: Sequence[_StepLike]


class MissingStepRef(BaseModel):
    """一个缺料 key 在哪条用例的哪一步、出现在 action 还是 expected。"""

    testcase_id: str
    step_number: int
    where: str
    """``action`` 或 ``expected`` 之一。"""


class MissingDataAlert(BaseModel):
    """非阻断告警：一个 key 在用例文本里出现但 resolver 没合并到。"""

    key: str
    detected_in_steps: list[MissingStepRef] = Field(default_factory=list)
    will_synthesize: bool = True
    """前端徽章用：True 表示 AI 会在执行时调 ``platform_synthesize_data`` 自造。"""


def extract_template_keys(text: str | None) -> list[str]:
    """提取 ``{{key}}`` 占位中的 key（保持出现顺序，去重）。"""
    if not text:
        return []
    seen: dict[str, None] = {}
    for m in _VAR_PATTERN.finditer(text):
        seen.setdefault(m.group(1), None)
    return list(seen)


async def preflight_data_check(
    testcases: Iterable[_TestcaseLike],
    resolver: TestDataResolver,
) -> list[MissingDataAlert]:
    """扫所有用例步骤，把 resolver 没有的 ``{{key}}`` 收集成告警列表。

    Args:
        testcases: 任意可迭代对象，每个元素需暴露 ``id`` 与 ``steps``。
        resolver: 已 ``build``/合并完成的 TestDataResolver；只读其 ``data``。

    Returns:
        告警列表（按 key 字母序），空列表表示**没有缺料**。
    """
    available = set(resolver.data.keys())
    by_key: dict[str, list[MissingStepRef]] = {}

    for tc in testcases:
        for step in tc.steps or []:
            for where, payload in (("action", step.action), ("expected", step.expected_result)):
                for key in extract_template_keys(payload):
                    if key in available:
                        continue
                    by_key.setdefault(key, []).append(
                        MissingStepRef(
                            testcase_id=str(tc.id),
                            step_number=step.step_number,
                            where=where,
                        ),
                    )

    return [
        MissingDataAlert(key=key, detected_in_steps=refs)
        for key, refs in sorted(by_key.items())
    ]


__all__ = [
    "MissingDataAlert",
    "MissingStepRef",
    "extract_template_keys",
    "preflight_data_check",
]
