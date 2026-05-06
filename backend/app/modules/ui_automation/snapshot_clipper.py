"""SnapshotClipper — accessibility snapshot 裁剪 + Token 治理。

设计文档：``docs/PHASE2_DESIGN.md`` §3.4。

UI 测试每步都把页面 ARIA snapshot 灌给 LLM。长页面单次可达 5–10K tokens，
5 用例 × 8 步 × 5K = 200K tokens / 次执行，不裁剪直接破产。本模块提供四
种裁剪策略，按"主区裁剪 → 字符上限 → diff 增量 → ref 索引"层层叠加：

| 策略 | 实现 | 收益 |
|---|---|---|
| 主区裁剪 | 启发式抽取 ``<main>`` / ``[role=main]`` 子树，缺失则取 body 但去掉 header/footer/nav | snapshot 体积减半 |
| 字符上限 | ``MAX_SNAPSHOT_CHARS`` 默认 3000，超额按"焦点优先 + 广度优先"截断 | 防长页面爆炸 |
| Diff 增量 | 第二步起只喂"上次 → 本次"的差异块 | 减少 60%+ 重复 token |
| Ref 缓存 | 维护 ``ref → role/name`` 映射，模型用 ref 索引避免重复看 tree | 后续 prompt 更短 |

**不强假设 snapshot 格式**：
``@playwright/mcp`` 的 snapshot 输出本质是 YAML/markdown 风格的层次化文本
（每行 ``role/name [ref=xxx]``）。本模块把它当**行结构化文本**处理，不解析
具体语法 —— 这样将来换 MCP server / Playwright 升级 snapshot 格式时只需
微调启发式正则，不用整体重写。
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field

# ── 默认配置（可被 ENV 覆盖，方便部署期调整不改代码）─────────────────
MAX_SNAPSHOT_CHARS = int(os.getenv("UI_SNAPSHOT_MAX_CHARS", "3000"))
DIFF_CONTEXT_LINES = int(os.getenv("UI_SNAPSHOT_DIFF_CONTEXT", "2"))

# 主区识别启发式（按优先级匹配）
_MAIN_REGION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*-?\s*main\b", re.IGNORECASE),
    re.compile(r"\[role=main\]", re.IGNORECASE),
    re.compile(r"<main\b", re.IGNORECASE),
    re.compile(r"\bregion\s+\"main\"", re.IGNORECASE),
]

# 噪音 region：当主区识别失败、退回 body 模式时去掉这些段
_NOISE_REGION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*-?\s*(banner|navigation|contentinfo|complementary)\b", re.IGNORECASE),
    re.compile(r"\[role=(banner|navigation|contentinfo|complementary)\]", re.IGNORECASE),
    re.compile(r"<(header|footer|nav|aside)\b", re.IGNORECASE),
]

# Snapshot 中的 element ref：``[ref=e123]`` / ``ref="e123"`` 都兼容
_REF_PATTERN = re.compile(r"\[?ref\s*=\s*[\"']?([A-Za-z0-9_\-:.]+)[\"']?\]?")


# ─── 主区裁剪 ────────────────────────────────────────────────────────


def clip_to_main_region(snapshot: str) -> str:
    """从 snapshot 中提取主内容区域。

    策略：
    1. 找第一个匹配 ``_MAIN_REGION_PATTERNS`` 的行，取该行 + 同缩进及更深
       缩进的所有后续行（直到出现更浅或同级非主区段）
    2. 如果没找到主区，回退："去掉 noise region 行"模式
    3. 任何情况下都返回非空字符串（最差返回原 snapshot）

    保留**整行**而非按字符切，避免把 ``[ref=e123]`` 这种关键标记切掉。
    """
    if not snapshot:
        return ""

    lines = snapshot.splitlines()
    main_start: int | None = None
    main_indent: int | None = None

    for idx, line in enumerate(lines):
        for pat in _MAIN_REGION_PATTERNS:
            if pat.search(line):
                main_start = idx
                main_indent = _leading_spaces(line)
                break
        if main_start is not None:
            break

    if main_start is None:
        return _drop_noise_regions(snapshot)

    # 从 main_start 起收集"同缩进及更深"的连续块；遇到更浅缩进且非空
    # 就停（说明回到了 main 同级别的下一段）。
    out: list[str] = [lines[main_start]]
    for line in lines[main_start + 1:]:
        if not line.strip():
            out.append(line)
            continue
        if _leading_spaces(line) <= (main_indent or 0):
            # 检查是否仍然是主区延续（罕见：主区被多个顶级节点表示）
            break
        out.append(line)
    return "\n".join(out)


def _drop_noise_regions(snapshot: str) -> str:
    """把 banner/footer/nav/aside 整段（以及其缩进子树）从 snapshot 移除。"""
    lines = snapshot.splitlines()
    out: list[str] = []
    skipping_until_indent: int | None = None

    for line in lines:
        if not line.strip():
            if skipping_until_indent is None:
                out.append(line)
            continue

        cur_indent = _leading_spaces(line)
        if skipping_until_indent is not None:
            if cur_indent > skipping_until_indent:
                continue  # 仍在 noise 子树内
            skipping_until_indent = None  # 跳出 noise 段

        if any(pat.search(line) for pat in _NOISE_REGION_PATTERNS):
            skipping_until_indent = cur_indent
            continue

        out.append(line)

    return "\n".join(out)


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


# ─── 字符上限截断 ─────────────────────────────────────────────────────


def clip_to_char_limit(
    snapshot: str,
    max_chars: int = MAX_SNAPSHOT_CHARS,
    *,
    focus_hint: str | None = None,
) -> str:
    """把 snapshot 限制在 ``max_chars`` 字符内。

    策略：
    - ``focus_hint``（典型：上一步操作的 ref / role 关键词）作为优先保留
      锚点：先包含 focus 行附近的上下文，再向四周扩张直到打满预算
    - 没有 focus 时按"前 80% + 末尾 20%"截断，因为 snapshot 末尾常含
      footer 元素，但开头是页面主结构
    - 截断处插入 ``\n... (clipped) ...\n`` 标记，让模型知道有省略
    """
    if not snapshot or len(snapshot) <= max_chars:
        return snapshot

    if focus_hint:
        focused = _clip_around_focus(snapshot, max_chars, focus_hint)
        if focused is not None:
            return focused

    head_budget = int(max_chars * 0.8)
    tail_budget = max_chars - head_budget - len(_CLIP_MARKER)
    if tail_budget < 0:
        tail_budget = 0
    return snapshot[:head_budget] + _CLIP_MARKER + snapshot[-tail_budget:] if tail_budget else (
        snapshot[:head_budget] + _CLIP_MARKER
    )


_CLIP_MARKER = "\n... (clipped) ...\n"


def _clip_around_focus(snapshot: str, max_chars: int, focus_hint: str) -> str | None:
    """以 focus_hint 在 snapshot 中的位置为中心展开 max_chars 字符。

    找不到 focus_hint 时返回 None，让调用方走 fallback。
    """
    pos = snapshot.find(focus_hint)
    if pos < 0:
        return None
    half = max_chars // 2
    start = max(0, pos - half)
    end = min(len(snapshot), pos + half)
    if end - start >= len(snapshot):
        return snapshot
    parts = []
    if start > 0:
        parts.append(_CLIP_MARKER.lstrip("\n"))
    parts.append(snapshot[start:end])
    if end < len(snapshot):
        parts.append(_CLIP_MARKER.rstrip("\n"))
    return "".join(parts)


# ─── Diff 增量 ────────────────────────────────────────────────────────


def diff_snapshots(
    prev: str,
    curr: str,
    *,
    context: int = DIFF_CONTEXT_LINES,
) -> str:
    """生成 prev→curr 的 unified diff（默认上下文 2 行）。

    返回纯文本 diff，无前缀文件名头部（与 git diff 不同），方便直接塞进
    LLM context。如果两个 snapshot 完全相同，返回空字符串。

    适用场景：第二步及以后的 snapshot 注入 → 只让模型看变化部分，省 60%+
    token。第一步 / diff 过大时退回完整 snapshot（由调用方决策）。
    """
    if prev == curr:
        return ""
    diff_lines = difflib.unified_diff(
        prev.splitlines(),
        curr.splitlines(),
        n=context,
        lineterm="",
    )
    # 去掉 difflib 默认的前两行 ``--- ``/``+++ ``，对模型无意义
    out: list[str] = []
    for i, line in enumerate(diff_lines):
        if i < 2 and (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        out.append(line)
    return "\n".join(out)


# ─── Ref 缓存 ─────────────────────────────────────────────────────────


@dataclass
class RefCache:
    """ARIA snapshot 中 ``[ref=xxx]`` 标记的索引。

    每次 ``update(snapshot)`` 把出现的 ref → 该 ref 所在行的精简描述
    （role + name + 关键属性）存起来。后续模型可以用短 ref 引用元素，
    StepRunner 在校验/调试时也能用 ``get(ref)`` 快速反查。

    只存"出现过的 ref"，不做语义解析；如果 MCP server 在某次 snapshot
    中没再返回某个 ref，``get`` 仍返回历史值（保留 5 步历史的 LRU），
    避免"上一步还在的元素这步看不到了"的瞬态问题。
    """

    history_size: int = 5
    _per_step: list[dict[str, str]] = field(default_factory=list, init=False, repr=False)

    @property
    def size(self) -> int:
        """当前缓存中独立 ref 的数量（跨步去重）。"""
        seen: set[str] = set()
        for snapshot in self._per_step:
            seen.update(snapshot)
        return len(seen)

    def update(self, snapshot: str) -> int:
        """从 snapshot 抽取所有 ref → 摘要，存入本步缓存。返回本步抽到几个 ref。"""
        new_step: dict[str, str] = {}
        for line in snapshot.splitlines():
            m = _REF_PATTERN.search(line)
            if not m:
                continue
            ref = m.group(1)
            if ref not in new_step:
                new_step[ref] = line.strip()
        self._per_step.append(new_step)
        if len(self._per_step) > self.history_size:
            self._per_step.pop(0)
        return len(new_step)

    def get(self, ref: str) -> str | None:
        """查 ref 的最近一次出现描述。从最新步往回找（LRU）。"""
        for snapshot in reversed(self._per_step):
            if ref in snapshot:
                return snapshot[ref]
        return None

    def reset(self) -> None:
        """新 execution / 新页面前清空。"""
        self._per_step.clear()


# ─── 一站式 helper ────────────────────────────────────────────────────


@dataclass
class ClippedSnapshot:
    """裁剪后产出，供 StepRunner 决定怎么注入 LLM context。"""

    text: str
    """实际要喂给 LLM 的文本（可能是完整裁剪 snapshot，也可能是 diff）。"""

    is_diff: bool
    """True 表示 ``text`` 是 diff 而非完整 snapshot。"""

    original_chars: int
    """原始 snapshot 字符数（裁剪前）。"""

    clipped_chars: int
    """裁剪后字符数。"""

    @property
    def saved_ratio(self) -> float:
        """节省比例。0.0 = 没节省；0.7 = 省了 70%。"""
        if self.original_chars <= 0:
            return 0.0
        return 1.0 - (self.clipped_chars / self.original_chars)


def clip_for_llm(
    snapshot: str,
    *,
    prev_snapshot: str | None = None,
    max_chars: int = MAX_SNAPSHOT_CHARS,
    focus_hint: str | None = None,
    enable_diff: bool = True,
) -> ClippedSnapshot:
    """主入口：把 raw snapshot 裁成可直接喂 LLM 的文本。

    决策树：
    1. 主区裁剪 → 得到 ``trimmed``
    2. 如果 ``prev_snapshot`` 存在且 ``enable_diff=True`` → 算 diff
        - diff 比 trimmed 短 → 用 diff（标记 is_diff=True）
        - 否则用 trimmed
    3. 字符上限截断（focus_hint 优先）
    """
    original = snapshot or ""
    trimmed = clip_to_main_region(original)

    text = trimmed
    is_diff = False
    if enable_diff and prev_snapshot:
        prev_trimmed = clip_to_main_region(prev_snapshot)
        diff_text = diff_snapshots(prev_trimmed, trimmed)
        if diff_text and len(diff_text) < len(trimmed):
            text = diff_text
            is_diff = True

    text = clip_to_char_limit(text, max_chars=max_chars, focus_hint=focus_hint)
    return ClippedSnapshot(
        text=text,
        is_diff=is_diff,
        original_chars=len(original),
        clipped_chars=len(text),
    )
