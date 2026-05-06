"""单条用例「数据可信度」评级。

与设计文档 PHASE2_DESIGN §3.6.9 对齐：
``data_confidence`` 与「功能通过 / 失败」正交独立字段。
"""

from __future__ import annotations

from typing import Any, Literal

ConfidenceLiteral = Literal["reliable", "synthesized", "data_failure"]


def evaluate_case_confidence(
    synthesized_data: list[dict[str, Any]] | None,
    data_failures: list[dict[str, Any]] | None,
) -> ConfidenceLiteral:
    """依据当前用例累积的自造记录 / 数据失败标记产出评级。

    优先级：
    1. 任一 ``platform_mark_data_failure`` → ``data_failure``
    2. 否则若 ``platform_synthesize_data`` 产生记录 → ``synthesized``
    3. 否则 → ``reliable``（数据来源全部为显式物料合并）

    Args:
        synthesized_data: ``current_case_log_synth`` 累积的条目列表。
        data_failures: ``current_case_mark_data_failure`` 累积条目列表。
    """
    failures = list(data_failures or [])
    synth = list(synthesized_data or [])
    if failures:
        return "data_failure"
    if synth:
        return "synthesized"
    return "reliable"
