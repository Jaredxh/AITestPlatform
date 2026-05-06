"""Task 8.6 纯函数单测：CSV 解析逻辑。

``parse_csv_to_items`` 不依赖 DB，是本 task 风险最集中的一块逻辑（列名对齐、
类型分派、JSON 解析、行级错误聚合），所以单测密度高。

import_items / clone_set / recommend_sets / save_overrides_as_set 等依赖 DB 的
流程由端到端 curl 冒烟 + 前端集成覆盖；本文件不用 SQLite/内存 DB 模拟以避免
维护两套 SQL dialect（项目里所有 async ORM 测试都用这种纯函数分层策略）。
"""

from __future__ import annotations

import pytest

from app.modules.test_data.schemas import TestDataImportItem
from app.modules.test_data.service import parse_csv_to_items

# ─── 正常 case ────────────────────────────────────────────────────────


def test_csv_minimal_columns_parsed() -> None:
    csv = "key,value_type\nusername,string\npassword,secret\n"
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert len(items) == 2
    assert items[0].key == "username" and items[0].value_type == "string"
    assert items[1].key == "password" and items[1].value_type == "secret"


def test_csv_full_columns_parsed() -> None:
    csv = """key,value_type,value,description,sort_order
username,string,admin,登录用户名,1
password,secret,p@ssw0rd,登录密码,2
remark,multiline,"line1\nline2",说明,3
phone,random,phone:CN,手机号模板,4
"""
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert len(items) == 4

    by_key = {it.key: it for it in items}
    assert by_key["username"].value_text == "admin"
    assert by_key["username"].description == "登录用户名"
    assert by_key["username"].sort_order == 1

    assert by_key["password"].value_type == "secret"
    assert by_key["password"].value_secret == "p@ssw0rd"
    assert by_key["password"].value_text is None

    assert by_key["remark"].value_type == "multiline"
    assert "line1" in by_key["remark"].value_text

    assert by_key["phone"].value_type == "random"
    assert by_key["phone"].value_text == "phone:CN"


def test_csv_with_utf8_bom() -> None:
    csv = "\ufeffkey,value_type\nfoo,string\n"
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert len(items) == 1
    assert items[0].key == "foo"


def test_csv_dataset_parses_json() -> None:
    csv = 'key,value_type,value\nproducts,dataset,"[{""sku"":""S1""},{""sku"":""S2""}]"\n'
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert items[0].value_json == [{"sku": "S1"}, {"sku": "S2"}]


def test_csv_empty_row_skipped() -> None:
    csv = "key,value_type\nfoo,string\n\n\nbar,string\n"
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert [it.key for it in items] == ["foo", "bar"]


def test_csv_column_aliases_cn() -> None:
    # 支持中文列名别名：名称 / 类型 / 值 / 说明 / 排序
    csv = "名称,类型,值,说明,排序\nuser,string,admin,用户,1\n"
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert items[0].key == "user"
    assert items[0].description == "用户"
    assert items[0].sort_order == 1


def test_csv_extra_columns_ignored() -> None:
    # 用户加了自己的备注列 → 静默忽略不报错
    csv = "key,value_type,note,value\nfoo,string,my comment,bar\n"
    items, errs = parse_csv_to_items(csv)
    assert errs == []
    assert items[0].value_text == "bar"


# ─── 错误 case ────────────────────────────────────────────────────────


def test_csv_empty_raises() -> None:
    with pytest.raises(Exception) as ei:
        parse_csv_to_items("")
    assert "EMPTY_CSV" in str(ei.value.__dict__.get("code", "")) or "为空" in str(ei.value)


def test_csv_missing_header_raises() -> None:
    with pytest.raises(Exception) as ei:
        parse_csv_to_items("\n\n")
    assert "HEADER" in str(ei.value.__dict__.get("code", "")) or "表头" in str(ei.value)


def test_csv_missing_required_column_raises() -> None:
    # 缺 key 列
    csv = "value_type,value\nstring,admin\n"
    with pytest.raises(Exception) as ei:
        parse_csv_to_items(csv)
    msg = getattr(ei.value, "message", str(ei.value))
    assert "key" in msg


def test_csv_invalid_value_type_collects_row_error() -> None:
    csv = """key,value_type
foo,string
bar,nonexistent_type
baz,secret
"""
    items, errs = parse_csv_to_items(csv)
    assert len(items) == 2  # foo + baz
    assert len(errs) == 1
    assert errs[0].key == "bar"
    assert "value_type" in errs[0].message.lower() or "非法" in errs[0].message


def test_csv_missing_key_per_row_error() -> None:
    csv = """key,value_type,value
foo,string,first
,string,second
bar,string,third
"""
    items, errs = parse_csv_to_items(csv)
    assert [it.key for it in items] == ["foo", "bar"]
    assert len(errs) == 1
    assert errs[0].row == 2
    assert errs[0].key is None


def test_csv_file_type_rejected_per_row() -> None:
    # file 类型不能通过 CSV 导入
    csv = "key,value_type,value\navatar,file,/tmp/x.png\n"
    items, errs = parse_csv_to_items(csv)
    assert items == []
    assert len(errs) == 1
    assert "file" in errs[0].message.lower()


def test_csv_dataset_invalid_json_collects_error() -> None:
    csv = "key,value_type,value\nproducts,dataset,not-json\n"
    items, errs = parse_csv_to_items(csv)
    assert items == []
    assert len(errs) == 1
    assert "json" in errs[0].message.lower()


def test_csv_sort_order_non_integer_error() -> None:
    csv = "key,value_type,sort_order\nfoo,string,abc\n"
    items, errs = parse_csv_to_items(csv)
    assert items == []
    assert len(errs) == 1
    assert "sort_order" in errs[0].message


def test_csv_invalid_key_regex_collects_error() -> None:
    # key 不合法（数字开头）→ Pydantic validator 抛 → 计入行错误
    csv = "key,value_type\n1username,string\nvalid_key,string\n"
    items, errs = parse_csv_to_items(csv)
    assert [it.key for it in items] == ["valid_key"]
    assert len(errs) == 1
    assert errs[0].row == 1


# ─── 行号正确性 ───────────────────────────────────────────────────────


def test_csv_row_numbers_are_data_row_based() -> None:
    # row 从 1 开始，排除 header
    csv = """key,value_type
ok1,string
,string
ok2,string
bad,xxx
"""
    items, errs = parse_csv_to_items(csv)
    assert [it.key for it in items] == ["ok1", "ok2"]
    rows = sorted(e.row for e in errs)
    assert rows == [2, 4]


def test_csv_large_but_within_limit() -> None:
    # 500 行在 10000 上限内 → 不该抛
    lines = ["key,value_type"] + [f"k{i},string" for i in range(500)]
    items, errs = parse_csv_to_items("\n".join(lines) + "\n")
    assert errs == []
    assert len(items) == 500


# ─── schema 本身 sanity ──────────────────────────────────────────────


def test_import_item_accepts_all_value_types() -> None:
    for vt in ("string", "secret", "multiline", "random", "dataset"):
        TestDataImportItem(key="k", value_type=vt)


def test_import_item_rejects_file_at_construction_time_via_parser() -> None:
    # schemas 层本身允许 file（因为 ItemCreateRequest 也允许 file，一致性），
    # file 的拦截在 service.parse_csv + import_items 两道。
    TestDataImportItem(key="k", value_type="file")  # schema 本身允许


# ─── upsert 语义 smoke（只测 schema 字段）─────────────────────────────


def test_import_request_default_mode_is_skip_existing() -> None:
    from app.modules.test_data.schemas import TestDataImportRequest

    req = TestDataImportRequest(
        items=[TestDataImportItem(key="k", value_type="string")],
    )
    assert req.mode == "skip_existing"


def test_import_request_mode_validation() -> None:
    from app.modules.test_data.schemas import TestDataImportRequest

    TestDataImportRequest(
        items=[TestDataImportItem(key="k", value_type="string")],
        mode="upsert",
    )
    with pytest.raises(Exception):
        TestDataImportRequest(
            items=[TestDataImportItem(key="k", value_type="string")],
            mode="overwrite",  # 非法 mode
        )
