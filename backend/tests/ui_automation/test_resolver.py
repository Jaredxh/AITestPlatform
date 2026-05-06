"""TestDataResolver：合并优先级、模板、清单、finalize、缓存首写。"""

from __future__ import annotations

from app.core import crypto
from app.modules.ui_automation.confidence_evaluator import evaluate_case_confidence
from app.modules.ui_automation.test_data_resolver import (
    TestDataItem,
    TestDataResolver,
    merge_item_layers,
)


def _items(**kwargs: str) -> list[TestDataItem]:
    return [TestDataItem.adhoc(k, v) for k, v in kwargs.items()]


def test_merge_item_layers_later_wins() -> None:
    personal = _items(a="p", b="p")
    project = _items(a="proj", c="proj")
    merged = merge_item_layers(personal, project)
    assert merged["a"].value_text == "proj"
    assert merged["b"].value_text == "p"
    assert merged["c"].value_text == "proj"


def test_five_layer_priority_loaded_over_project_manual_over_all() -> None:
    personal = _items(x="1")
    project = _items(x="2", y="py")
    env = _items(x="3", z="env")
    loaded = _items(x="4", y="ld")
    merged = merge_item_layers(personal, project, env, loaded)
    assert merged["x"].value_text == "4"
    assert merged["y"].value_text == "ld"
    assert merged["z"].value_text == "env"

    from app.modules.ui_automation.test_data_resolver import _apply_manual

    _apply_manual(merged, {"x": "manual", "extra": "m"})
    assert merged["x"].value_text == "manual"
    assert merged["extra"].value_text == "m"


def test_with_case_overrides_order_simulation() -> None:
    """环境与弹窗之间插入用例层：loaded 应覆盖 testcase 同 key。"""
    personal: list[TestDataItem] = []
    project = _items(k="proj")
    env = _items(k="env")
    testcase = _items(k="tc")
    loaded = _items(k="loaded")
    merged = merge_item_layers(personal, project, env, testcase, loaded)
    assert merged["k"].value_text == "loaded"


def test_render_template_secret_and_file_placeholders() -> None:
    secret_enc = crypto.encrypt("hunter2")
    data = {
        "pwd": TestDataItem(
            key="pwd",
            value_type="secret",
            value_encrypted=secret_enc,
        ),
        "doc": TestDataItem(
            key="doc",
            value_type="file",
            file_path="/uploads/proj/set/u_foo.pdf",
        ),
        "name": TestDataItem(key="name", value_type="string", value_text="Ada"),
    }
    r = TestDataResolver.from_merge_dict(data)
    out = r.render_template("pw={{pwd}} path={{doc}} hi {{name}} missing={{nope}}")
    assert out == "pw=<secret:pwd> path=<file:doc> hi Ada missing={{nope}}"


def test_render_manifest_contains_rules_and_fallback_section() -> None:
    r = TestDataResolver.from_merge_dict(
        {"u": TestDataItem(key="u", value_type="string", value_text="x", description="账号")},
    )
    md = r.render_manifest_markdown()
    assert "## 可用测试物料" in md
    assert "platform_get_secret" in md
    assert "## 缺料兜底规则" in md
    assert "platform_synthesize_data" in md


def test_serialize_for_audit_redacts_secret() -> None:
    enc = crypto.encrypt("sekrit")
    r = TestDataResolver.from_merge_dict(
        {"s": TestDataItem(key="s", value_type="secret", value_encrypted=enc)},
    )
    blob = r.serialize_for_audit()["s"]
    assert blob["value"] == "<secret:redacted>"
    assert "sekrit" not in str(blob)


def test_finalize_case_three_confidence_levels() -> None:
    r = TestDataResolver.from_merge_dict({"a": TestDataItem.adhoc("a", "1")})
    r.reset_case_state()
    assert r.finalize_case()["data_confidence"] == "reliable"

    r.reset_case_state()
    r.current_case_log_synth("k", "v", "heuristic")
    assert r.finalize_case()["data_confidence"] == "synthesized"

    r.reset_case_state()
    r.current_case_log_synth("k", "v", "heuristic")
    r.current_case_mark_data_failure("k", "bad")
    assert r.finalize_case()["data_confidence"] == "data_failure"

    r.reset_case_state()
    r.current_case_mark_data_failure("x", "only_fail")
    assert r.finalize_case()["data_confidence"] == "data_failure"


def test_evaluate_case_confidence_matches_resolver_contract() -> None:
    assert evaluate_case_confidence([], []) == "reliable"
    assert evaluate_case_confidence([{"key": "a"}], []) == "synthesized"
    assert evaluate_case_confidence([], [{"key": "a"}]) == "data_failure"
    assert evaluate_case_confidence([{"key": "a"}], [{"key": "b"}]) == "data_failure"


def test_cache_synthesized_first_write_wins() -> None:
    r = TestDataResolver.from_merge_dict({})
    r.cache_synthesized("phone", "111", "heuristic")
    assert r.data["phone"].value_text == "111"
    r.cache_synthesized("phone", "222", "heuristic")
    assert r.data["phone"].value_text == "111"
    synth_rows = [x for x in r._case_synth_log if x.get("key") == "phone"]
    assert len(synth_rows) == 1


def test_resolve_secret_roundtrip() -> None:
    enc = crypto.encrypt("plain")
    item = TestDataItem(key="k", value_type="secret", value_encrypted=enc)
    assert item.resolve_secret() == "plain"


def test_random_realize_mutates_text() -> None:
    it = TestDataItem(key="r", value_type="random", value_text="digits:6")
    it.realize()
    assert it.value_text is not None
    assert len(it.value_text) == 6
    assert it.value_text.isdigit()
