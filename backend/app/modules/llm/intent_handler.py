"""Simple intent recognition for chat commands.

Detects when a user message requests a structured action (review, generate testcases)
rather than plain conversation, and dispatches accordingly.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.requirements.models import RequirementDocument

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    REVIEW = "review"
    GENERATE_TESTCASES = "generate_testcases"
    # NOTE：原 ``RUN_UI_TEST`` 关键词驱动意图（Task 9.6 引入）已在二期验收后
    # 取消——关键词触发不可靠且与「测试用例 → 执行 UI」UI 入口职责重复。
    # 三期会用 agent skill 的方式重新实现，让 LLM 通过显式工具调用而非
    # 正则匹配触发 UI 测试。
    CHAT = "chat"


@dataclass
class DetectedIntent:
    intent: IntentType
    confidence: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)
    original_text: str = ""


# ── Keyword-based patterns ──

_REVIEW_PATTERNS = [
    re.compile(r"(?:帮我|请|麻烦)?(?:评审|审查|review)\s*(?:一下)?(?:项目\s*\S+?\s*的?\s*)?(?:最新的?)?(?:需求)?(?:文档)?", re.I),
    re.compile(r"(?:对|把)\s*(?:这个|这份|最新的?)?\s*(?:需求)?文档\s*(?:进行|做)\s*(?:一下)?\s*(?:评审|审查)", re.I),
    re.compile(r"评审.*文档", re.I),
    re.compile(r"review\s+(?:the\s+)?(?:latest\s+)?(?:requirement\s+)?doc", re.I),
]

_GENERATE_PATTERNS = [
    re.compile(r"(?:帮我|请|麻烦)?(?:根据|基于)?\s*(?:需求)?\s*(?:文档)?\s*(?:生成|创建|编写)\s*(?:一下)?\s*(?:测试)?用例", re.I),
    re.compile(r"(?:生成|创建)\s*(?:测试)?用例", re.I),
    re.compile(r"generate\s+(?:test\s*)?cases?", re.I),
    re.compile(r"(?:帮我|请)?从.*(?:生成|创建).*用例", re.I),
]

def detect_intent(text: str) -> DetectedIntent:
    """Detect user intent from message text using keyword patterns.

    NOTE：原"关键词触发 UI 自动化执行"分支已下线（二期验收反馈：识别不准、
    与 UI 入口职责重叠）。三期通过 agent skill 重新实现。
    """
    stripped = text.strip()

    for pattern in _REVIEW_PATTERNS:
        if pattern.search(stripped):
            doc_name = _extract_doc_name(stripped)
            return DetectedIntent(
                intent=IntentType.REVIEW,
                params={"doc_hint": doc_name} if doc_name else {},
                original_text=stripped,
            )

    for pattern in _GENERATE_PATTERNS:
        if pattern.search(stripped):
            doc_name = _extract_doc_name(stripped)
            return DetectedIntent(
                intent=IntentType.GENERATE_TESTCASES,
                params={"doc_hint": doc_name} if doc_name else {},
                original_text=stripped,
            )

    return DetectedIntent(intent=IntentType.CHAT, original_text=stripped)


def _extract_doc_name(text: str) -> str | None:
    """Try to extract a document name reference from the user message."""
    patterns = [
        re.compile(r"[\"\"「](.+?)[\"\"」]"),
        re.compile(r"《(.+?)》"),
        re.compile(r"文档\s*[:：]\s*(\S+)"),
    ]
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(1).strip()
    return None


async def resolve_document(
    db: AsyncSession,
    project_id: uuid.UUID,
    doc_hint: str | None = None,
) -> RequirementDocument | None:
    """Find a document in the project, optionally matching a hint string.
    Falls back to the most recently uploaded parsed document.
    """
    if doc_hint:
        result = await db.execute(
            select(RequirementDocument)
            .where(
                RequirementDocument.project_id == project_id,
                RequirementDocument.status == "parsed",
                RequirementDocument.filename.ilike(f"%{doc_hint}%"),
            )
            .order_by(RequirementDocument.created_at.desc())
            .limit(1)
        )
        doc = result.scalar_one_or_none()
        if doc:
            return doc

    result = await db.execute(
        select(RequirementDocument)
        .where(
            RequirementDocument.project_id == project_id,
            RequirementDocument.status == "parsed",
        )
        .order_by(RequirementDocument.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def format_review_result(review_data: dict) -> str:
    """Format review result as a structured markdown message with metadata marker."""
    score = review_data.get("overall_score", "N/A")
    summary = review_data.get("summary", "")
    dimensions = review_data.get("dimensions", {})
    issues = review_data.get("issues", [])

    lines = [
        "## 📋 需求评审结果\n",
        f"**综合评分：{score} / 100**\n",
        f"### 总体评价\n{summary}\n",
    ]

    if dimensions:
        lines.append("### 维度评分\n")
        dim_labels = {
            "completeness": "完整性",
            "clarity": "清晰性",
            "consistency": "一致性",
            "testability": "可测试性",
            "feasibility": "可行性",
        }
        lines.append("| 维度 | 评分 | 说明 |")
        lines.append("|------|------|------|")
        for key, label in dim_labels.items():
            d = dimensions.get(key, {})
            lines.append(f"| {label} | {d.get('score', '-')} | {d.get('comment', '-')} |")
        lines.append("")

    if issues:
        lines.append(f"### 发现问题 ({len(issues)} 个)\n")
        severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        for i, issue in enumerate(issues, 1):
            sev = issue.get("severity", "medium")
            emoji = severity_emoji.get(sev, "⚪")
            lines.append(f"{emoji} **{i}. {issue.get('category', '问题')}** [{sev}]")
            lines.append(f"   {issue.get('description', '')}")
            if issue.get("location"):
                lines.append(f"   📍 位置: {issue['location']}")
            if issue.get("suggestion"):
                lines.append(f"   💡 建议: {issue['suggestion']}")
            lines.append("")

    return "\n".join(lines)


def format_generation_result(testcases: list[dict], batch_id: str) -> str:
    """Format generated testcases as a markdown message."""
    lines = [
        "## 🧪 AI 生成测试用例\n",
        f"共生成 **{len(testcases)}** 条用例（批次 ID: `{batch_id[:8]}...`）\n",
    ]

    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    for i, tc in enumerate(testcases, 1):
        p = tc.get("priority", "medium")
        emoji = priority_emoji.get(p, "⚪")
        lines.append(f"### {emoji} {i}. {tc.get('title', '未命名用例')}")
        if tc.get("precondition"):
            lines.append(f"**前置条件**: {tc['precondition']}")
        steps = tc.get("steps", [])
        if steps:
            lines.append("\n| 步骤 | 操作 | 预期结果 |")
            lines.append("|------|------|----------|")
            for s in steps:
                lines.append(
                    f"| {s.get('step_number', '-')} "
                    f"| {s.get('action', '-')} "
                    f"| {s.get('expected_result', '-')} |"
                )
        lines.append("")

    lines.append(
        "\n> 💡 你可以到 **测试用例** 页面查看并管理这些用例，"
        "或在此继续对话调整生成策略。"
    )

    return "\n".join(lines)


def build_action_metadata(
    intent: IntentType,
    *,
    review_id: str | None = None,
    review_data: dict | None = None,
    batch_id: str | None = None,
    testcases: list[dict] | None = None,
    document_name: str | None = None,
    error: str | None = None,
) -> dict:
    """Build structured metadata to store in ChatMessage.meta_data for frontend rendering."""
    meta: dict[str, Any] = {"action_type": intent.value}

    if document_name:
        meta["document_name"] = document_name

    if intent == IntentType.REVIEW:
        if review_id:
            meta["review_id"] = review_id
        if review_data:
            meta["overall_score"] = review_data.get("overall_score")
            meta["summary"] = review_data.get("summary")
            meta["dimensions"] = review_data.get("dimensions")
            meta["issues_count"] = len(review_data.get("issues", []))

    elif intent == IntentType.GENERATE_TESTCASES:
        if batch_id:
            meta["batch_id"] = batch_id
        if testcases is not None:
            meta["generated_count"] = len(testcases)

    if error:
        meta["error"] = error

    return meta
