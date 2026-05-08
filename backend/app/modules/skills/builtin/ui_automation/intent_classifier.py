"""ui_automation NLU 意图分类器（Phase 13 / Task 13.0）。

设计依据：``docs/PHASE3_DESIGN.md §10.0``。两段式校验防"昨天跑用例失败率"
被"跑/用例"关键词召回误触发执行：

::

    [Stage 1] SkillRouter 关键词召回（与 Phase 12 现有逻辑一致）
        ↓ ui_automation 进入 candidate 池
    [Stage 2] IntentClassifier.classify  ← 本模块
        ↓
    action != execute_test 或 conf < 0.7
        → 把 ui_automation 候选剔除（避免 LLM 看到"诱惑"被错误调用）

两层兜底，最便宜的方案放最前：

- **Layer 1 规则**：覆盖 80% 简单 case，零 token、零 LLM 延迟；本 task 范围
  内**完整实现**（DoD 三条样例全部命中）。
- **Layer 2 LLM**：仅在 Layer 1 ``confidence < 0.85`` 时兜底；按 ``llm_caller``
  注入式实现——不耦合任何具体 provider，调用方传入"接收 prompt 返回 JSON"
  的 awaitable 即可。task 13.0 不强求 Layer 2 实际跑（设计文档 §10.0.2
  把 Layer 2 列为"覆盖剩余 20%"，DoD 可接受默认走 Layer 1 的 fallback 行为）；
  接入 LLM 调用器留给后续 PR / task 13.5 物料语义化时一并打通即可。

不会破坏 Phase 12 任何现有路径——只是 ``compose()`` 末尾的"剔除候选"
新逻辑；从未触发到 ui_automation 的 chat 上下文走完全相同的代码分支。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

logger = logging.getLogger(__name__)


#: 设计文档 §10.0.3：默认仅对内置 ``system_ui_automation`` 做意图二段校验；
#: 用户自定义 skill 不强加 NLU 校验，触发逻辑保持 Phase 12 自由度。
UI_AUTOMATION_INTENT_GUARDED: frozenset[str] = frozenset({
    "system_ui_automation",
})


IntentAction = Literal[
    "execute_test",
    "query_history",
    "edit_testcase",
    "learn",
    "other",
]


@dataclass(frozen=True)
class IntentResult:
    """NLU 输出结构。

    - ``action``：枚举之一；调用方据此决定是否保留 ui_automation 候选。
    - ``target``：抽出的执行对象描述（"登录" / "#123" / None）；下游
      ``search_test_cases`` 用作查询输入。
    - ``confidence``：``[0.0, 1.0]``；``compose`` 默认阈值 0.7。
    - ``slots``：进一步抽取的 slot（``env_hint`` / ``data_hint`` / ``case_ref``），
      M1 task 13.0 阶段先返回空字典；M2 接入物料语义化后再填。
    - ``reason``：决策依据；用于前端 chat 历史调试可视化。
    """

    action: IntentAction
    target: str | None
    confidence: float
    slots: dict[str, str] = field(default_factory=dict)
    reason: str = ""


# ─────────────────── Layer 1 — 规则正则 ────────────────────────────
#
# 关键设计：**先排除非执行型意图**（query / learn / edit），最后才落到
# execute_test。这样"昨天跑用例失败率"会先被 query_history 拦下，不会走到
# "跑/用例" 的 execute 兜底分支造成误触发。

_QUERY_HISTORY_PATTERNS = [
    re.compile(r"(失败率|通过率|成功率|失败数|通过数)"),
    re.compile(r"(执行历史|执行记录|跑过的|跑了哪些|历史结果)"),
    re.compile(r"(昨天|前天|上周|上次|最近一次|刚才|刚刚).{0,4}(跑|执行|测|run)"),
    re.compile(r"(统计|汇总|看看.{0,4}(数据|结果))"),
]

_LEARN_PATTERNS = [
    re.compile(r"(怎么写|如何写|怎样写|教我写)"),
    re.compile(r"(怎么|如何|怎样).{0,4}(学|入门|理解|区分)"),
    re.compile(r"(什么是|是什么|为什么需要|为什么要)"),
    re.compile(r"(教我|讲讲|介绍下|解释下)"),
]

_EDIT_PATTERNS = [
    re.compile(r"(改|修改|编辑|调整|更新).{0,8}(用例|步骤|预期|断言|参数)"),
    re.compile(r"(把|将).{0,12}(改成|改为|换成|更新为)"),
    re.compile(r"(用例|步骤|预期).{0,6}(改成|改为|换成)"),
]

_CASE_REF_PATTERN = re.compile(r"(?P<ref>(?:#|TC[-_]?)\d+)", re.IGNORECASE)
_EXECUTE_VERB_PATTERN = re.compile(
    r"(跑下|跑一下|跑跑|跑个|帮我跑|帮跑|执行下|执行一下|执行|run\b|启动)",
    re.IGNORECASE,
)
_EXECUTE_TARGET_PATTERN = re.compile(
    # "跑/执行/run" 后面紧跟（可有"下/一下"等修饰）的目标短语；目标由"非
    # 标点+空格"的字符组成，遇到"用例 / 流程 / 一下 / 一下子"等结尾词截断。
    r"(?:跑下|跑一下|跑跑|跑个|帮我跑|帮跑|执行下|执行一下|执行|run|启动)\s*"
    r"(?P<target>[^\s,.，。！？!?]{1,30})",
    re.IGNORECASE,
)


def _extract_case_ref(message: str) -> str | None:
    m = _CASE_REF_PATTERN.search(message)
    if m:
        return m.group("ref")
    return None


def _extract_execute_target(message: str) -> str | None:
    """抽取动作动词后的目标短语；优先返回带编号的 ref。"""
    ref = _extract_case_ref(message)
    if ref:
        return ref
    m = _EXECUTE_TARGET_PATTERN.search(message)
    if m:
        target = m.group("target").strip()
        # 截掉常见尾词，避免把"用例"两个字带进来污染 search_test_cases
        for tail in ("用例", "流程", "测试", "case", "Case"):
            if target.endswith(tail) and len(target) > len(tail):
                target = target[: -len(tail)].strip()
        return target or None
    return None


def _rule_based_classify(message: str) -> IntentResult:
    """Layer 1：纯正则；零 token、零依赖、零 LLM 延迟。"""
    text = (message or "").strip()
    if not text:
        return IntentResult(
            action="other",
            target=None,
            confidence=1.0,
            reason="empty_message",
        )

    # 1) query_history 优先 —— 防"昨天跑用例失败率"被 execute 误判
    for pat in _QUERY_HISTORY_PATTERNS:
        if pat.search(text):
            return IntentResult(
                action="query_history",
                target=None,
                confidence=0.90,
                reason=f"matched_query_pattern:{pat.pattern}",
            )

    # 2) learn —— "怎么写好用例" / "如何入门"
    for pat in _LEARN_PATTERNS:
        if pat.search(text):
            return IntentResult(
                action="learn",
                target=None,
                confidence=0.85,
                reason=f"matched_learn_pattern:{pat.pattern}",
            )

    # 3) edit_testcase —— "改 / 修改用例"
    for pat in _EDIT_PATTERNS:
        if pat.search(text):
            return IntentResult(
                action="edit_testcase",
                target=None,
                confidence=0.85,
                reason=f"matched_edit_pattern:{pat.pattern}",
            )

    # 4) execute_test —— 含动作动词；编号命中给 0.95，否则给 0.85
    case_ref = _extract_case_ref(text)
    has_verb = bool(_EXECUTE_VERB_PATTERN.search(text))
    if case_ref and has_verb:
        return IntentResult(
            action="execute_test",
            target=case_ref,
            confidence=0.95,
            reason="execute_verb+case_ref",
        )
    if case_ref:
        # 有编号但无动作动词：仍倾向 execute（用户提到 #123 一般为想跑/查），
        # 但置信度降到 0.7 以下让 LLM 反问澄清。
        return IntentResult(
            action="execute_test",
            target=case_ref,
            confidence=0.65,
            reason="case_ref_only",
        )
    if has_verb:
        target = _extract_execute_target(text)
        # 设计 §10.0.4：纯动词 + 名词性目标置信度 0.85；裸"用例"等低信息目标
        # 调到 0.55 让前端反问而不是直接执行。
        if target:
            conf = 0.85 if len(target) >= 2 else 0.55
            return IntentResult(
                action="execute_test",
                target=target,
                confidence=conf,
                reason=f"execute_verb+target:{target!r}",
            )
        return IntentResult(
            action="execute_test",
            target=None,
            confidence=0.55,
            reason="execute_verb_no_target",
        )

    # 5) 兜底 —— other / 普通问答；conf 故意低于 0.85 让 Layer 2（如已注入）
    # 兜底；Layer 2 缺席时调用方判 conf<0.7 走"非执行"路径，自然不会触发
    # ui_automation 误调用。
    return IntentResult(
        action="other",
        target=None,
        confidence=0.40,
        reason="no_rule_matched",
    )


# ─────────────────── Layer 2 — LLM 兑底（注入式） ────────────────────
#
# 以"接收 prompt 返回 JSON 字符串"的 callable 注入；不耦合任何 provider。
# task 13.0 不在主路径强制跑 Layer 2（Layer 1 已能覆盖 §10.0.4 全部反例）；
# 后续 task 接入时只需在 ``compose()`` 调用处把 ``llm_classifier`` 传进来即可。

LLMClassifierCallable = Callable[[str], Awaitable[str]]


_LLM_PROMPT_TEMPLATE = (
    "判断这条用户消息的真实意图。可选项（只能选一个）：\n"
    "  - execute_test     用户想立即执行 UI 自动化用例\n"
    "  - query_history    用户想查执行历史 / 统计数据\n"
    "  - edit_testcase    用户想编辑用例内容\n"
    "  - learn            用户想学习 / 提问相关概念\n"
    "  - other            其它（普通对话 / 不确定）\n\n"
    "输出严格 JSON：{{\"action\":\"...\",\"target\":\"...或null\",\"confidence\":0~1}}\n"
    "用户消息：\"{user_message}\""
)


async def _llm_based_classify(
    message: str,
    *,
    llm_classifier: LLMClassifierCallable,
) -> IntentResult:
    """Layer 2：调注入的 LLM 拿一次结构化 JSON。"""
    prompt = _LLM_PROMPT_TEMPLATE.format(user_message=message.replace('"', '\\"'))
    try:
        raw = await asyncio.wait_for(llm_classifier(prompt), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("intent_classifier layer2 timeout; falling back to layer1 conservative")
        return IntentResult(
            action="other",
            target=None,
            confidence=0.40,
            reason="layer2_timeout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent_classifier layer2 failed: %s", exc)
        return IntentResult(
            action="other",
            target=None,
            confidence=0.40,
            reason=f"layer2_error:{type(exc).__name__}",
        )

    return _parse_llm_json(raw)


def _parse_llm_json(raw: str) -> IntentResult:
    """容错解析 LLM 返回 —— 模型偶尔会带 ```json ``` 围栏或前后多写解释。"""
    import json

    text = (raw or "").strip()
    # 剥离常见 markdown 围栏
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    # 抓第一段 ``{...}``
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return IntentResult(
            action="other",
            target=None,
            confidence=0.40,
            reason="layer2_invalid_json",
        )
    action = data.get("action")
    if action not in ("execute_test", "query_history", "edit_testcase", "learn", "other"):
        action = "other"
    target = data.get("target")
    if target in (None, "null", ""):
        target = None
    elif not isinstance(target, str):
        target = str(target)
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return IntentResult(
        action=action,
        target=target,
        confidence=conf,
        reason="layer2_llm",
    )


# ─────────────────── 缓存（防 SSE 多次拉取重复 classify） ─────────────
#
# DoD：单条 user_message 最多 1 次 classify，结果按 ``(session_id, message_hash)``
# 缓存 60s。这里走 process-local 字典 + 读时清扫，简单够用；多 worker 场景
# 各自维护一份缓存也能保证 60s 内 ≤ N(workers) 次调用，远低于"每 SSE chunk
# 都跑一次"的 worst case。

_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 1024
_cache: dict[tuple[uuid.UUID | None, str], tuple[float, IntentResult]] = {}
_cache_lock = asyncio.Lock()


def _cache_key(session_id: uuid.UUID | None, message: str) -> tuple[uuid.UUID | None, str]:
    digest = hashlib.sha1(message.encode("utf-8"), usedforsecurity=False).hexdigest()
    return (session_id, digest)


async def _cache_get(key: tuple[uuid.UUID | None, str]) -> IntentResult | None:
    now = time.monotonic()
    async with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        deadline, result = entry
        if deadline < now:
            _cache.pop(key, None)
            return None
        return result


async def _cache_set(key: tuple[uuid.UUID | None, str], result: IntentResult) -> None:
    deadline = time.monotonic() + _CACHE_TTL_SECONDS
    async with _cache_lock:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            # 简易清扫：丢弃最早过期的一半
            now = time.monotonic()
            stale = [k for k, (d, _r) in _cache.items() if d < now]
            for k in stale:
                _cache.pop(k, None)
            if len(_cache) >= _CACHE_MAX_ENTRIES:
                # 仍超限就直接丢一半最旧的，避免内存无界增长
                items = sorted(_cache.items(), key=lambda kv: kv[1][0])
                for k, _ in items[: len(items) // 2]:
                    _cache.pop(k, None)
        _cache[key] = (deadline, result)


def _cache_clear_for_test() -> None:
    """仅供测试调用以隔离用例。生产代码不会调。"""
    _cache.clear()


# ─────────────────── 主入口 ──────────────────────────────────────────


async def classify(
    user_message: str,
    *,
    session_id: uuid.UUID | None = None,
    llm_classifier: LLMClassifierCallable | None = None,
) -> IntentResult:
    """两段式分类主入口。

    - 命中缓存直接返回（同一 session_id+message 60s 内不重复跑）。
    - Layer 1 ``confidence ≥ 0.85`` 视为足够稳，直接返回。
    - 否则若注入了 ``llm_classifier`` 跑 Layer 2 兜底；未注入则原样返回 Layer 1
      （置信度自然偏低，调用方按 ``conf < 0.7`` 路由到"非执行"分支）。
    """
    key = _cache_key(session_id, user_message or "")
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    rule_result = _rule_based_classify(user_message)

    if rule_result.confidence >= 0.85 or llm_classifier is None:
        await _cache_set(key, rule_result)
        return rule_result

    layer2 = await _llm_based_classify(user_message, llm_classifier=llm_classifier)
    # Layer 2 置信度更高时取 Layer 2，否则保留 Layer 1（防 LLM 抽风把高置信
    # 规则结果踩低）。
    final = layer2 if layer2.confidence >= rule_result.confidence else rule_result
    await _cache_set(key, final)
    return final
