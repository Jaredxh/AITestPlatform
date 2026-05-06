"""AssertionJudge — 步骤断言判定（Task 9.5）。

设计文档：``docs/PHASE2_DESIGN.md`` §3.3.1 + §3.3.3。

判定策略（按优先级降序）：

1. **无 expected** → ``passed=True``（method=``no_expected``）
   开发未填 expected 的步骤视为"操作完成即通过"，不强制判定
2. **无 snapshot** → ``passed=False``（method=``skipped``）
   StepRunner 跑完没拿到 snapshot 通常是 tool 全部被拦或全 fail，标记失败
3. **整段精确包含** → ``passed=True``（method=``text_search``）
   ``expected.strip()`` 直接出现在 snapshot 里 —— 最稳的命中
4. **多关键词全部命中** → ``passed=True``（method=``text_search``）
   按空白 / 中英文标点切词，长度 ≥ 2 的 token 全部出现
5. **LLM 模糊判断** → 走兜底（method=``llm``）
   把 expected + snapshot 喂给 LLM，要求严格 JSON 输出
6. LLM 不可用（``llm_config=None`` 或调用失败） → ``passed=False``
   （method=``text_search``，原因里说明纯文本未命中）

设计原则：
- 每一种 method 都给出 ``reason`` + ``evidence``（可空），方便前端展示
- 永远返回 ``AssertionVerdict``，不抛错；LLM 异常被吞为 ``llm_unavailable``
- 默认 LLM 完成函数走 ``app.modules.llm.providers.complete_chat``，
  测试可注入任意 mock 函数（typeable via ``CompletionFn`` Protocol）
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.modules.llm.providers import complete_chat as _default_complete_chat

logger = logging.getLogger(__name__)


JudgeMethod = Literal["text_search", "llm", "skipped", "no_expected", "llm_unavailable"]

# LLM 完成函数的最小契约：(messages, **opts) -> str（最终回复文本）
CompletionFn = Callable[..., Awaitable[str]]


@dataclass
class AssertionVerdict:
    """断言判定结果。所有字段都设计成可直接 JSON 序列化喂给 SSE / DB。"""

    passed: bool
    reason: str
    evidence: str = ""
    method: JudgeMethod = "text_search"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "evidence": self.evidence,
            "method": self.method,
        }


@dataclass
class AssertionLLMConfig:
    """传给 LLM fallback 的最小配置（避免 import LLMConfig ORM）。"""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    """thinking 模式 LLM（GLM-4.x / 火山方舟 doubao 1.5 thinking-pro / o1 等）会在
    ``reasoning_content`` 里花数百 token 内部思考，``max_tokens`` 偏小时会出现
    "reasoning 用满 → final content 截空"，断言阶段拿到 ``content=""`` →
    ``_try_parse_json_object`` 返回 None → reason 写进库的就是 "LLM 输出无法解析
    为 JSON：" 后面是空字符串（用户实际看到的故障 #f6513ebb 即是此症）。

    2048 是经验值：对 GLM-4 thinking + 我们这条简短 prompt（typical 200~400 input
    tokens），既给 thinking 留 1500+ token 的余地，又给 final JSON 输出留 500+。
    成本上影响小（断言只在文本搜索失败时才走 LLM）。"""


_TOKEN_SPLIT_RE = re.compile(r"[\s,，;；、\.\。:：!?！？/\\\(\)\[\]\{\}<>《》「」]+")
_MIN_TOKEN_LEN = 2

_LLM_PROMPT_TEMPLATE = """判定 UI 测试步骤是否符合预期。

步骤描述：{step_description}
预期结果：{expected_result}

执行后页面 accessibility 快照（已裁剪）：
```
{snapshot}
```

严格只输出 JSON（不要 Markdown 代码块），结构：
{{"passed": true|false, "reason": "<中文一句>", "evidence": "<快照里命中的关键片段，可空字符串>"}}"""


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _split_tokens(expected: str) -> list[str]:
    """expected 拆成"必须命中"的关键词列表（去空白、去过短词、去重保序）。"""
    toks: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT_RE.split(expected.strip()):
        t = raw.strip()
        if len(t) < _MIN_TOKEN_LEN:
            continue
        if t in seen:
            continue
        seen.add(t)
        toks.append(t)
    return toks


def _try_parse_json_object(content: str) -> dict | None:
    """从 LLM 文本里提取首个 JSON 对象；失败返回 None。"""
    if not content:
        return None
    text = content.strip()
    # 去掉常见的 Markdown 代码栅栏
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class AssertionJudge:
    """步骤断言判定器。一个 execution 复用一个实例足够。"""

    __test__ = False

    def __init__(self, *, completion_fn: CompletionFn | None = None) -> None:
        self._completion_fn = completion_fn or _default_complete_chat

    async def judge(
        self,
        *,
        expected: str | None,
        snapshot: str | None,
        step_description: str | None = None,
        llm_config: AssertionLLMConfig | None = None,
    ) -> AssertionVerdict:
        """对单步骤做断言判定。

        :param expected: ``rendered_expected`` —— 已经过模板替换
        :param snapshot: StepRunner 收尾时 ``last_snapshot_text`` 或裁剪后
        :param step_description: 仅给 LLM fallback 用，不影响纯文本判定
        :param llm_config: 不为 None 时启用 LLM 兜底
        """
        if not (expected and expected.strip()):
            return AssertionVerdict(
                passed=True,
                reason="未提供预期结果，按 StepRunner 收尾即视为通过",
                method="no_expected",
            )

        if not snapshot or not snapshot.strip():
            return AssertionVerdict(
                passed=False,
                reason="缺少 snapshot，无法做文本比对",
                method="skipped",
            )

        exp_clean = expected.strip()

        # 整段精确包含
        if exp_clean in snapshot:
            evidence = exp_clean if len(exp_clean) <= 120 else exp_clean[:117] + "..."
            return AssertionVerdict(
                passed=True,
                reason=f"snapshot 中找到了完整 expected 文本 “{exp_clean[:40]}{'…' if len(exp_clean) > 40 else ''}”",
                evidence=evidence,
                method="text_search",
            )

        # 多关键词全部命中
        tokens = _split_tokens(exp_clean)
        if tokens and all(t in snapshot for t in tokens):
            return AssertionVerdict(
                passed=True,
                reason=f"snapshot 命中全部 {len(tokens)} 个关键词：{', '.join(tokens)}",
                evidence=", ".join(tokens),
                method="text_search",
            )

        # LLM 兜底
        if llm_config is None:
            missing = [t for t in tokens if t not in snapshot] if tokens else [exp_clean]
            return AssertionVerdict(
                passed=False,
                reason=f"snapshot 未命中关键词：{', '.join(missing[:5])}",
                evidence="",
                method="text_search",
            )

        return await self._llm_judge(
            expected=exp_clean,
            snapshot=snapshot,
            step_description=step_description or "",
            llm_config=llm_config,
        )

    async def _llm_judge(
        self,
        *,
        expected: str,
        snapshot: str,
        step_description: str,
        llm_config: AssertionLLMConfig,
    ) -> AssertionVerdict:
        prompt = _LLM_PROMPT_TEMPLATE.format(
            step_description=step_description or "(未提供)",
            expected_result=expected,
            snapshot=snapshot,
        )
        try:
            content = await self._completion_fn(
                provider=llm_config.provider,
                model=llm_config.model,
                messages=[{"role": "user", "content": prompt}],
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
                temperature=llm_config.temperature,
                max_tokens=llm_config.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AssertionJudge LLM call failed: %s", exc)
            return AssertionVerdict(
                passed=False,
                reason=f"LLM 兜底失败（{type(exc).__name__}），请检查 LLM 配置",
                evidence="",
                method="llm_unavailable",
            )

        parsed = _try_parse_json_object(content)
        if parsed is None:
            # 空 content 是 thinking 模式典型症状：``content=""`` 通常意味着
            # ``reasoning_content`` 把 max_tokens 用光了。给出一句明确的修复建议
            # 而不是把空串拼到 reason 里——否则用户看到的是 "LLM 输出无法解析
            # 为 JSON：" 后面什么都没有的截断式错误（实际故障 #f6513ebb）。
            stripped = (content or "").strip()
            if not stripped:
                reason_text = (
                    "LLM 返回空内容（可能是 thinking 模式 max_tokens 不够，"
                    "或 LLM 网关限流）；请检查 LLM 配置 / 加大 max_tokens 后重试"
                )
            else:
                reason_text = f"LLM 输出无法解析为 JSON：{stripped[:200]}"
            return AssertionVerdict(
                passed=False,
                reason=reason_text,
                evidence="",
                method="llm_unavailable",
            )

        passed = bool(parsed.get("passed"))
        reason = str(parsed.get("reason") or ("LLM 判定通过" if passed else "LLM 判定未通过"))
        evidence = str(parsed.get("evidence") or "")
        return AssertionVerdict(
            passed=passed,
            reason=reason[:1000],
            evidence=evidence[:1000],
            method="llm",
        )


__all__ = [
    "AssertionJudge",
    "AssertionLLMConfig",
    "AssertionVerdict",
    "JudgeMethod",
]
