"""测试物料运行时：五级合并、模板替换、清单 markdown、自造缓存。

与设计 §3.6.1–§3.6.4、Task 9.1 对齐；platform tools 在 Task 9.2 接入。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import crypto
from app.modules.test_data.models import TestDataItem as TestDataItemORM
from app.modules.test_data.models import TestDataSet
from app.modules.test_data.random_generator import generate as generate_random
from app.modules.ui_automation.confidence_evaluator import evaluate_case_confidence

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_VAR_PATTERN = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


def merge_item_layers(*layers: Iterable[TestDataItem]) -> dict[str, TestDataItem]:
    """多层物料条目合并：**后出现**的同 key 覆盖先出现的。"""
    merged: dict[str, TestDataItem] = {}
    for layer in layers:
        for item in layer:
            merged[item.key] = item
    return merged


def _realize_random_all(data: Mapping[str, TestDataItem]) -> None:
    for it in data.values():
        it.realize()


@dataclass
class TestDataItem:
    """运行时表示（可由 ORM 转译或手工构造）。"""

    __test__ = False

    key: str
    value_type: str
    value_text: str | None = None
    value_encrypted: str | None = None
    value_json: dict[str, Any] | list[Any] | None = None
    file_path: str | None = None
    file_size: int | None = None
    file_mime: str | None = None
    description: str | None = None
    synthetic_source: str | None = None
    """非 None 表示来自 ``platform_synthesize_data`` / ``cache_synthesized``。"""
    source_set_id: uuid.UUID | None = None
    """源物料集 id；adhoc / manual override / 自造时为 None。"""
    source_set_name: str | None = None
    """源物料集名称（落库快照时直接显示给用户）。"""

    def copy(self) -> TestDataItem:
        return TestDataItem(
            key=self.key,
            value_type=self.value_type,
            value_text=self.value_text,
            value_encrypted=self.value_encrypted,
            value_json=self._clone_json(self.value_json),
            file_path=self.file_path,
            file_size=self.file_size,
            file_mime=self.file_mime,
            description=self.description,
            synthetic_source=self.synthetic_source,
            source_set_id=self.source_set_id,
            source_set_name=self.source_set_name,
        )

    @staticmethod
    def _clone_json(
        val: dict[str, Any] | list[Any] | None,
    ) -> dict[str, Any] | list[Any] | None:
        if val is None:
            return None
        return json.loads(json.dumps(val, ensure_ascii=False))

    @classmethod
    def from_orm(
        cls,
        row: TestDataItemORM,
        *,
        source_set_id: uuid.UUID | None = None,
        source_set_name: str | None = None,
    ) -> TestDataItem:
        return cls(
            key=row.key,
            value_type=row.value_type,
            value_text=row.value_text,
            value_encrypted=row.value_encrypted,
            value_json=row.value_json,  # type: ignore[assignment]
            file_path=row.file_path,
            file_size=row.file_size,
            file_mime=row.file_mime,
            description=row.description,
            synthetic_source=None,
            source_set_id=source_set_id,
            source_set_name=source_set_name,
        )

    @classmethod
    def adhoc(cls, key: str, value: Any) -> TestDataItem:
        if isinstance(value, (dict, list)):
            return cls(key=key, value_type="dataset", value_json=value, description=None)
        return cls(key=key, value_type="string", value_text=str(value), description=None)

    @classmethod
    def synthetic(cls, key: str, value: str, source: str) -> TestDataItem:
        return cls(
            key=key,
            value_type="string",
            value_text=value,
            synthetic_source=source,
        )

    def overridden_with(self, value: Any) -> TestDataItem:
        base = self.copy()
        if isinstance(value, (dict, list)):
            base.value_type = "dataset"
            base.value_json = value  # type: ignore[assignment]
            base.value_text = None
            base.value_encrypted = None
            base.file_path = None
            base.synthetic_source = None
            return base
        base.value_type = "string"
        base.value_text = str(value)
        base.value_encrypted = None
        base.value_json = None
        base.file_path = None
        base.synthetic_source = None
        return base

    def realize(self) -> None:
        if self.value_type != "random":
            return
        tpl = self.value_text or ""
        self.value_text = generate_random(tpl)

    def resolve_secret(self) -> str:
        if self.value_type != "secret":
            raise ValueError(f"物料 {self.key!r} 不是 secret 类型")
        if not self.value_encrypted:
            return ""
        return crypto.decrypt(self.value_encrypted)

    def display_safe_value(self, *, max_len: int = 120) -> str:
        if self.value_type == "secret":
            return "●●●●"
        if self.value_type == "file":
            return Path(self.file_path).name if self.file_path else "-"
        if self.value_type == "dataset":
            raw = json.dumps(self.value_json, ensure_ascii=False)
            return raw if len(raw) <= max_len else raw[: max_len - 1] + "…"
        if self.value_type == "multiline" and self.value_text:
            line = self.value_text.splitlines()[0]
            return line if len(line) <= max_len else line[: max_len - 1] + "…"
        txt = self.value_text or ""
        return txt if len(txt) <= max_len else txt[: max_len - 1] + "…"

    def template_substitution_value(self) -> str:
        if self.value_type == "dataset":
            return json.dumps(self.value_json, ensure_ascii=False)
        if self.value_type == "file":
            return self.file_path or ""
        return self.value_text or ""

    def to_audit_blob(self) -> dict[str, Any]:
        blob: dict[str, Any] = {
            "key": self.key,
            "value_type": self.value_type,
            "description": self.description,
        }
        if self.synthetic_source is not None:
            blob["synthetic_source"] = self.synthetic_source
        if self.source_set_id is not None:
            blob["source_set_id"] = str(self.source_set_id)
        if self.source_set_name is not None:
            blob["source_set_name"] = self.source_set_name
        if self.value_type == "secret":
            blob["value"] = "<secret:redacted>"
            return blob
        if self.value_type == "file":
            blob["file_name"] = Path(self.file_path).name if self.file_path else None
            blob["file_size"] = self.file_size
            blob["file_mime"] = self.file_mime
            return blob
        if self.value_type == "dataset":
            blob["value_json"] = self.value_json
            return blob
        blob["value_text"] = self.value_text
        return blob


@runtime_checkable
class ExecutionLike(Protocol):
    triggered_by: uuid.UUID
    project_id: uuid.UUID
    environment_id: uuid.UUID | None


async def _ordered_sets_with_items(
    db: AsyncSession,
    set_ids: Sequence[uuid.UUID],
) -> list[TestDataSet]:
    if not set_ids:
        return []
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.id.in_(set_ids))
        .order_by(TestDataSet.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    order_map = {sid: i for i, sid in enumerate(set_ids)}
    return sorted(rows, key=lambda ds: order_map.get(ds.id, 10**9))


def _flatten_set_items(data_sets: Iterable[TestDataSet]) -> list[TestDataItem]:
    """把多个 TestDataSet 摊平成 TestDataItem 列表，**附带源集合 id/name**。

    源信息让 ``serialize_for_audit`` 能做"只展示本次执行配置的物料集"过滤
    （Task 验收反馈：物料快照不应展示项目里所有物料，只展示本次配置的）。
    """
    out: list[TestDataItem] = []
    for ds in data_sets:
        items = list(ds.items or [])
        items.sort(key=lambda r: (r.sort_order, r.key))
        for row in items:
            out.append(
                TestDataItem.from_orm(
                    row,
                    source_set_id=ds.id,
                    source_set_name=ds.name,
                ),
            )
    return out


async def _load_scope_personal(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> list[TestDataItem]:
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(
            TestDataSet.project_id == project_id,
            TestDataSet.scope == "personal",
            TestDataSet.owner_id == owner_id,
        )
        .order_by(TestDataSet.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    return _flatten_set_items(rows)


async def _load_scope_project(db: AsyncSession, *, project_id: uuid.UUID) -> list[TestDataItem]:
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(TestDataSet.project_id == project_id, TestDataSet.scope == "project")
        .order_by(TestDataSet.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    return _flatten_set_items(rows)


async def _load_scope_environment(
    db: AsyncSession,
    *,
    environment_id: uuid.UUID,
) -> list[TestDataItem]:
    stmt = (
        select(TestDataSet)
        .options(selectinload(TestDataSet.items))
        .where(
            TestDataSet.scope == "environment",
            TestDataSet.environment_id == environment_id,
        )
        .order_by(TestDataSet.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    return _flatten_set_items(rows)


async def _items_from_set_id_list(
    db: AsyncSession,
    set_ids: Sequence[uuid.UUID],
) -> list[TestDataItem]:
    sets_ordered = await _ordered_sets_with_items(db, set_ids)
    return _flatten_set_items(sets_ordered)


def _apply_manual(merged: dict[str, TestDataItem], manual: Mapping[str, Any]) -> None:
    for key, raw in manual.items():
        if key in merged:
            merged[key] = merged[key].overridden_with(raw)
        else:
            merged[key] = TestDataItem.adhoc(key, raw)


class TestDataResolver:
    """五级合并 + 模板替换 + 清单；按用例累积 synthesize / failure 供 finalize。"""

    __test__ = False

    def __init__(
        self,
        *,
        data: dict[str, TestDataItem],
        execution: ExecutionLike | None,
        db: AsyncSession | None,
        layer_personal: list[TestDataItem],
        layer_project: list[TestDataItem],
        layer_environment: list[TestDataItem],
        layer_loaded: list[TestDataItem],
        manual_overrides: Mapping[str, Any],
    ) -> None:
        self.data = data
        self.execution = execution
        self._db = db
        self._layer_personal = layer_personal
        self._layer_project = layer_project
        self._layer_environment = layer_environment
        self._layer_loaded = layer_loaded
        self._manual_overrides = dict(manual_overrides)
        self._case_synth_log: list[dict[str, Any]] = []
        self._case_failures: list[dict[str, Any]] = []
        self._synth_first_write: dict[str, str] = {}

    @classmethod
    def from_merge_dict(
        cls,
        merged: Mapping[str, TestDataItem],
        *,
        manual_overrides: Mapping[str, Any] | None = None,
    ) -> TestDataResolver:
        """跳过 DB 的构造器（单测或桩）。假定 ``merged`` 已含 realized random。"""
        return cls(
            data=dict(merged),
            execution=None,
            db=None,
            layer_personal=[],
            layer_project=[],
            layer_environment=[],
            layer_loaded=[],
            manual_overrides=manual_overrides or {},
        )

    @classmethod
    async def build(
        cls,
        db: AsyncSession,
        execution: ExecutionLike,
        manual_overrides: Mapping[str, Any],
        loaded_set_ids: Sequence[uuid.UUID],
    ) -> TestDataResolver:
        """五级合并：个人 → 项目 → 环境（含环境默认集 id）→ 弹窗加载集 → 手动覆盖。"""
        personal = await _load_scope_personal(
            db, project_id=execution.project_id, owner_id=execution.triggered_by,
        )
        project = await _load_scope_project(db, project_id=execution.project_id)

        env_bind: list[TestDataItem] = []
        extra_env_ids: list[uuid.UUID] = []
        if execution.environment_id is not None:
            env_bind = await _load_scope_environment(db, environment_id=execution.environment_id)
            from app.modules.ui_automation.models import TestEnvironment

            env_row = (
                await db.execute(select(TestEnvironment).where(TestEnvironment.id == execution.environment_id))
            ).scalar_one_or_none()
            if env_row is not None:
                for sid in env_row.default_data_set_ids or []:
                    try:
                        extra_env_ids.append(uuid.UUID(str(sid)))
                    except (ValueError, TypeError):
                        continue
        env_extra_items = await _items_from_set_id_list(db, extra_env_ids)
        environment_layer = merge_item_layers(env_bind, env_extra_items).values()
        environment = list(environment_layer)

        loaded_items = await _items_from_set_id_list(db, loaded_set_ids)

        merged = merge_item_layers(
            personal,
            project,
            environment,
            loaded_items,
        )
        _apply_manual(merged, manual_overrides)
        _realize_random_all(merged)

        return cls(
            data=merged,
            execution=execution,
            db=db,
            layer_personal=personal,
            layer_project=project,
            layer_environment=environment,
            layer_loaded=loaded_items,
            manual_overrides=manual_overrides,
        )

    async def with_case_overrides(self, testcase_id: uuid.UUID) -> TestDataResolver:
        """并入用例 ``default_data_set_ids`` 对应条目（插在环境与弹窗加载集之间），其它层不变。"""
        if self._db is None:
            raise RuntimeError("with_case_overrides 需要 build() 时传入的 db session")

        from app.modules.testcases.models import Testcase

        tc_row = (
            await self._db.execute(select(Testcase).where(Testcase.id == testcase_id))
        ).scalar_one_or_none()
        tc_ids: list[uuid.UUID] = []
        if tc_row is not None:
            for sid in tc_row.default_data_set_ids or []:
                try:
                    tc_ids.append(uuid.UUID(str(sid)))
                except (ValueError, TypeError):
                    continue

        testcase_items = await _items_from_set_id_list(self._db, tc_ids)

        merged = merge_item_layers(
            self._layer_personal,
            self._layer_project,
            self._layer_environment,
            testcase_items,
            self._layer_loaded,
        )
        _apply_manual(merged, self._manual_overrides)
        _realize_random_all(merged)

        return TestDataResolver(
            data=merged,
            execution=self.execution,
            db=self._db,
            layer_personal=list(self._layer_personal),
            layer_project=list(self._layer_project),
            layer_environment=list(self._layer_environment),
            layer_loaded=list(self._layer_loaded),
            manual_overrides=self._manual_overrides,
        )

    def reset_case_state(self) -> None:
        self._case_synth_log.clear()
        self._case_failures.clear()
        self._synth_first_write.clear()

    def render_template(self, text: str) -> str:
        """Layer 1：``{{key}}`` → 值；secret / file 替换为占位（不进明文 prompt）。"""
        if not text:
            return text

        def replace(m: re.Match[str]) -> str:
            key = m.group(1)
            item = self.data.get(key)
            if item is None:
                return m.group(0)
            if item.value_type == "secret":
                return f"<secret:{key}>"
            if item.value_type == "file":
                return f"<file:{key}>"
            return item.template_substitution_value()

        return _VAR_PATTERN.sub(replace, text)

    def render_manifest_markdown(self) -> str:
        """Layer 2：物料表 + 使用规则 + 缺料兜底说明。"""
        if not self.data:
            return ""
        rows: list[str] = []
        for key in sorted(self.data):
            item = self.data[key]
            disp = item.display_safe_value()
            desc = item.description or "-"
            rows.append(f"| {key} | {item.value_type} | {desc} | {disp} |")
        return (
            "## 可用测试物料\n"
            "本次执行可使用以下物料（在用例步骤中遇到对应场景时引用）：\n\n"
            "| key | 类型 | 描述 | 当前值 |\n"
            "|-----|------|------|--------|\n"
            + "\n".join(rows)
            + "\n\n## 物料使用规则\n"
            "- 普通物料按值使用\n"
            "- secret 物料必须通过 `platform_get_secret(key)` tool 获取，**不要在 reasoning 中明文展示**\n"
            "- file 物料用 `platform_get_file(key)` 获取本地路径再喂给 `browser_set_input_files`\n"
            "- dataset 物料用 `platform_iter_dataset(key)` 迭代访问每条记录\n"
            "\n## 用例硬编码数据失败时的物料 fallback（重要！）\n"
            "用例步骤里**直接写死**的 ID / 账号 / 名称等字面值（没有 `{{...}}` 占位），\n"
            "可能只是用例作者举的示例占位，并非当前环境真实数据。判定流程：\n"
            "1. 先按步骤里的字面值操作；\n"
            "2. 若页面出现「未找到 / 不存在 / 无权限 / 提交失败 / 列表空」等信号，\n"
            "   **立即停止用原值反复重试**，转而调 `platform_get_test_data(key)` 列表上方表格里\n"
            "   **业务语义最匹配**的 key（如步骤里是「创作者 ID」，物料 key 含 `creator_id`、\n"
            "   `valid_user_id`、`existing_*` 等都属语义匹配）；\n"
            "3. 用物料里的真实值替换原占位，重试一次；reasoning 里要明确说明改用了哪个 key、原值\n"
            "   失败的具体信号；\n"
            "4. 物料里也确实没有匹配项时再 `platform_synthesize_data`；都不行最后才\n"
            "   `platform_mark_data_failure`。\n"
            "\n## 缺料兜底规则\n"
            "- 若步骤需要的物料缺失或未替换，可调用 `platform_synthesize_data` "
            "请求平台启发式 / AI 自造（需在 reasoning 中可观测）\n"
            "- 若确认数据环境不可用请调用 `platform_mark_data_failure` 标记；"
            "该用例最终会被标记为 `data_failure` 评级\n"
        )

    def serialize_for_audit(
        self,
        *,
        configured_set_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        """持久化快照：不含 secret 明文。

        ``configured_set_ids`` 给定时，**只保留**源集合在该列表里的条目，
        外加：manual override / synthetic / 无源条目（adhoc）。这是验收反馈
        「快照只显示本次执行配置的物料集明细」的实现：项目/个人 scope 自动
        合并进来的物料不会污染快照面板，让用户一眼看清"我配置的就是这些"。

        ``configured_set_ids = None`` 时回退到旧行为（导出全部合并物料），
        保留向后兼容。
        """
        allowed: set[uuid.UUID] | None = (
            {uuid.UUID(str(sid)) for sid in configured_set_ids}
            if configured_set_ids is not None
            else None
        )
        out: dict[str, Any] = {}
        for k in sorted(self.data):
            item = self.data[k]
            if allowed is not None:
                # source_set_id 为 None：adhoc / manual override / synthetic
                # 这些"非物料集"来源永远保留（用户的临时数据 + AI 自造）。
                if (
                    item.source_set_id is not None
                    and item.source_set_id not in allowed
                ):
                    continue
            out[k] = item.to_audit_blob()
        return out

    def cache_synthesized(
        self,
        key: str,
        value: str,
        source: str,
        *,
        hint: str | None = None,
    ) -> bool:
        """缓存自造值：**同一 key 以首次写入为准**。返回本次是否实际写入。"""
        if key in self._synth_first_write:
            return False
        self._synth_first_write[key] = value
        self.data[key] = TestDataItem.synthetic(key, value, source)
        self.current_case_log_synth(
            key,
            value,
            source,
            hint=hint if hint is not None else "cache_synthesized",
        )
        return True

    def current_case_log_synth(
        self,
        key: str,
        value: str,
        source: str,
        hint: str | None = None,
    ) -> None:
        row: dict[str, Any] = {"key": key, "value": value, "source": source}
        if hint is not None:
            row["hint"] = hint
        self._case_synth_log.append(row)

    def current_case_mark_data_failure(self, key: str, reason: str) -> None:
        self._case_failures.append({"key": key, "reason": reason})

    def finalize_case(self) -> dict[str, Any]:
        conf = evaluate_case_confidence(self._case_synth_log, self._case_failures)
        return {
            "synthesized_data": list(self._case_synth_log),
            "data_failures": list(self._case_failures),
            "data_confidence": conf,
        }


__all__ = [
    "ExecutionLike",
    "TestDataItem",
    "TestDataResolver",
    "merge_item_layers",
]
