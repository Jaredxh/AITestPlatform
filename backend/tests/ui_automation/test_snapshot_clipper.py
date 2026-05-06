"""Task 7.3 验证：snapshot_clipper 四种裁剪策略。

主区裁剪 / 字符上限 / diff 增量 / ref 缓存 — 全部覆盖。
"""

from __future__ import annotations

from app.modules.ui_automation.snapshot_clipper import (
    ClippedSnapshot,
    RefCache,
    clip_for_llm,
    clip_to_char_limit,
    clip_to_main_region,
    diff_snapshots,
)

# ─── 主区裁剪 ─────────────────────────────────────────────────────────


SAMPLE_WITH_MAIN = """\
- banner [ref=e1]
  - link "Logo" [ref=e2]
  - navigation [ref=e3]
- main [ref=e10]
  - heading "Welcome" [ref=e11]
  - form [ref=e12]
    - textbox "Email" [ref=e13]
    - textbox "Password" [ref=e14]
    - button "Login" [ref=e15]
- contentinfo [ref=e20]
  - link "Privacy" [ref=e21]
"""


def test_clip_to_main_extracts_main_subtree() -> None:
    out = clip_to_main_region(SAMPLE_WITH_MAIN)
    assert "main [ref=e10]" in out
    assert "heading \"Welcome\"" in out
    assert "button \"Login\"" in out
    # 不应包含 banner / contentinfo
    assert "banner" not in out
    assert "contentinfo" not in out


SAMPLE_WITHOUT_MAIN = """\
- banner [ref=e1]
  - heading "Site Title"
- region "Article" [ref=e10]
  - heading "Hello"
  - paragraph "World"
- contentinfo [ref=e20]
  - link "Footer"
"""


def test_clip_drops_noise_when_no_main() -> None:
    out = clip_to_main_region(SAMPLE_WITHOUT_MAIN)
    assert "Article" in out
    assert "Hello" in out
    # noise 被移除
    assert "banner" not in out
    assert "contentinfo" not in out
    assert "Footer" not in out


def test_clip_returns_empty_for_empty_input() -> None:
    assert clip_to_main_region("") == ""


# ─── 字符上限截断 ─────────────────────────────────────────────────────


def test_clip_char_limit_no_op_when_under() -> None:
    text = "a" * 100
    assert clip_to_char_limit(text, max_chars=200) == text


def test_clip_char_limit_inserts_marker_and_keeps_head_tail() -> None:
    text = "HEAD" + ("x" * 5000) + "TAIL"
    out = clip_to_char_limit(text, max_chars=500)
    assert len(out) <= 600  # 500 + clip marker
    assert out.startswith("HEAD")
    assert "(clipped)" in out
    assert out.endswith("TAIL") or "TAIL" in out[-100:]


def test_clip_char_limit_focus_keeps_anchor_visible() -> None:
    text = ("a" * 3000) + "FIND_ME" + ("b" * 3000)
    out = clip_to_char_limit(text, max_chars=400, focus_hint="FIND_ME")
    assert "FIND_ME" in out
    assert len(out) <= 500


# ─── Diff 增量 ────────────────────────────────────────────────────────


def test_diff_returns_empty_when_identical() -> None:
    assert diff_snapshots("same\nlines", "same\nlines") == ""


def test_diff_emits_changes_only() -> None:
    prev = "line1\nline2\nline3\n"
    curr = "line1\nLINE2_NEW\nline3\n"
    diff = diff_snapshots(prev, curr)
    assert diff
    assert "-line2" in diff
    assert "+LINE2_NEW" in diff


def test_diff_strips_filename_headers() -> None:
    """难免 difflib 默认带 ``--- ``/``+++ ``，对模型无意义，应被剥掉。"""
    diff = diff_snapshots("a", "b")
    lines = diff.splitlines()
    # 不以 ``--- `` / ``+++ `` 开头
    for line in lines:
        assert not line.startswith("--- ")
        assert not line.startswith("+++ ")


# ─── RefCache ────────────────────────────────────────────────────────


def test_ref_cache_indexes_each_step() -> None:
    cache = RefCache(history_size=3)
    n = cache.update(SAMPLE_WITH_MAIN)
    assert n > 0
    # 能查到主区里的 ref
    desc = cache.get("e15")
    assert desc is not None
    assert "Login" in desc


def test_ref_cache_remembers_across_steps_within_history() -> None:
    cache = RefCache(history_size=3)
    cache.update("- button [ref=e1]")  # step 1
    cache.update("- input [ref=e2]")   # step 2
    cache.update("- link [ref=e3]")    # step 3
    # 全部仍在 history
    assert cache.get("e1") is not None
    assert cache.get("e2") is not None
    assert cache.get("e3") is not None
    # 第 4 步把 e1 那一步挤出 LRU
    cache.update("- p [ref=e4]")
    assert cache.get("e1") is None
    assert cache.get("e4") is not None


def test_ref_cache_returns_none_for_unknown() -> None:
    cache = RefCache()
    cache.update("- div [ref=e10]")
    assert cache.get("not-a-ref") is None


def test_ref_cache_reset_clears_all() -> None:
    cache = RefCache()
    cache.update("- div [ref=e10]")
    assert cache.get("e10") is not None
    cache.reset()
    assert cache.get("e10") is None
    assert cache.size == 0


# ─── 一站式 clip_for_llm ──────────────────────────────────────────────


def test_clip_for_llm_returns_full_when_first_step() -> None:
    out = clip_for_llm(SAMPLE_WITH_MAIN)
    assert isinstance(out, ClippedSnapshot)
    assert out.is_diff is False
    assert "main" in out.text
    assert out.original_chars == len(SAMPLE_WITH_MAIN)


def test_clip_for_llm_uses_diff_when_smaller() -> None:
    """第二步：diff 比完整 trimmed snapshot 短 → 用 diff。"""
    prev = SAMPLE_WITH_MAIN
    curr = SAMPLE_WITH_MAIN.replace("Welcome", "Welcome Back")
    out = clip_for_llm(curr, prev_snapshot=prev)
    assert out.is_diff is True
    assert "Welcome Back" in out.text
    assert "Welcome" in out.text  # diff 也包含旧值


def test_clip_for_llm_falls_back_to_full_when_diff_useless() -> None:
    """当 prev/curr 完全无关时 diff 会比完整 snapshot 更长 → 退回完整。"""
    prev = "completely-different-content " * 200
    curr = SAMPLE_WITH_MAIN
    out = clip_for_llm(curr, prev_snapshot=prev)
    # diff 会很大，预期不是 diff
    assert out.is_diff is False


def test_clip_for_llm_respects_max_chars() -> None:
    huge = SAMPLE_WITH_MAIN * 200
    out = clip_for_llm(huge, max_chars=500)
    assert len(out.text) <= 600  # 500 + marker
    assert out.saved_ratio > 0.9


def test_clip_for_llm_can_disable_diff() -> None:
    prev = SAMPLE_WITH_MAIN
    curr = SAMPLE_WITH_MAIN.replace("Welcome", "Welcome Back")
    out = clip_for_llm(curr, prev_snapshot=prev, enable_diff=False)
    assert out.is_diff is False
