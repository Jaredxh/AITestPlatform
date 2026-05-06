"""物料相关的 6 个 ``platform_*`` 动态工具注册（Task 9.2）。

工具名格式 ``{execution_id}:platform_*`` 写入 ``TOOL_REGISTRY``，供 StepRunner
与 ``run_tool`` 使用。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.agent_tools import register_tool, unregister_tool
from app.modules.ui_automation.data_synthesizer import DataSynthesizer
from app.modules.ui_automation.test_data_resolver import TestDataResolver

_PLATFORM_SUFFIXES: tuple[str, ...] = (
    "platform_get_test_data",
    "platform_get_secret",
    "platform_get_file",
    "platform_iter_dataset",
    "platform_synthesize_data",
    "platform_mark_data_failure",
)


def _tool_name(execution_id: uuid.UUID | str, suffix: str) -> str:
    # 用 ``__`` 而非 ``:`` 作命名空间分隔符 —— OpenAI Chat 接口要求
    # ``tools[i].function.name`` 必须匹配 ``^[a-zA-Z0-9_-]+$``，``:`` 不在白名单
    # 会直接 400 BadRequest（实际触发于 ai_login 试跑端点切换 LLM provider 时）。
    # 选 ``__`` 因为：
    # 1) 合规：``_`` 在白名单内
    # 2) 不与 tool 原名冲突：所有 platform_* / browser_* 工具内部只有单下划线
    # 3) 不与 UUID 中的 ``-`` 冲突，剥前缀时不会误切
    return f"{execution_id}__{suffix}"


def _dataset_slice(
    value_json: dict[str, Any] | list[Any] | None,
    offset: int,
    limit: int,
) -> tuple[list[Any], int]:
    if value_json is None:
        return [], 0
    if isinstance(value_json, dict):
        rows: list[Any] = [value_json]
    elif isinstance(value_json, list):
        rows = list(value_json)
    else:
        rows = []
    total = len(rows)
    end = min(offset + max(1, limit), total)
    return rows[offset:end], total


def redact_tool_result_for_reasoning(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """StepRunner / flush_step 落库 reasoning 前调用：剥离 secret 明文。"""
    if not isinstance(result, dict):
        return result
    if not result.get("_test_data_secret_used"):
        return result
    safe = {k: v for k, v in result.items() if k != "value"}
    safe["_test_data_secret_used"] = True
    safe["_secret_reasoning_note"] = f"<secret used: {tool_name}; plaintext omitted>"
    return safe


def platform_tools_openai_schemas(*, execution_id: uuid.UUID | str) -> list[dict[str, Any]]:
    """已带 ``<execution_id>:`` 前缀的 OpenAI Chat tools，可直接传给 ``tools=``。"""
    ns = str(execution_id)
    specs = _schema_templates()
    out: list[dict[str, Any]] = []
    for row in specs:
        fn = row["function"]
        raw_name = fn["name"]
        out.append(
            {
                "type": row["type"],
                "function": {
                    **fn,
                    "name": _tool_name(ns, raw_name),
                },
            },
        )
    return out


def _schema_templates() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "platform_get_test_data",
                "description": (
                    "读取已合并物料条目的非敏感信息（secret 仅返回类型提示）。"
                    "**典型触发场景**：用例步骤里直接写死的 ID/账号/名称等占位值操作后页面出现"
                    "「未找到/不存在/无权限/列表空」等数据无效信号时，先用本工具按业务语义查找"
                    "真实物料（如步骤要查 creator_id，可尝试 'creator_id'、'valid_creator_id'、"
                    "'test_creator_id' 等语义匹配 key），用其真实值重试，避免反复用占位失败。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "物料 key"}},
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "platform_get_secret",
                "description": "解密并返回 secret 类型物料明文。结果勿写入 reasoning 日志。",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "platform_get_file",
                "description": "返回 file 类型物料的服务器路径，用于 browser_set_input_files。",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "platform_iter_dataset",
                "description": "分页读取 dataset 类型物料 JSON（数组或单对象视作一行）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "platform_synthesize_data",
                "description": (
                    "当模板变量缺失时自造临时测试数据（启发式优先，其次 LLM）。"
                    "完成后必须把返回值用于步骤并在 reasoning 中注明来源。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "hint": {"type": "string", "description": "业务语义提示"},
                        "value_type": {
                            "type": "string",
                            "description": "期望类型（string/multiline/dataset 等）",
                            "default": "string",
                        },
                    },
                    "required": ["key"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "platform_mark_data_failure",
                "description": "主动标记当前 key 的数据不可用；该用例会被评为 data_failure。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["key", "reason"],
                },
            },
        },
    ]


def register_data_tools(
    execution_id: uuid.UUID | str,
    resolver: TestDataResolver,
    *,
    db: AsyncSession | None = None,
    synthesizer: DataSynthesizer | None = None,
) -> list[str]:
    """注册 6 个 platform 工具，返回完整注册名列表。"""
    synth = synthesizer or DataSynthesizer(db=db)
    ns = str(execution_id)
    registered: list[str] = []

    async def _get_test_data(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        if not key:
            return {"error": "key required"}
        item = resolver.data.get(key)
        if item is None:
            return {"error": f"key '{key}' not found"}
        if item.value_type == "secret":
            return {
                "key": key,
                "value_type": "secret",
                "description": item.description,
                "note": "Use platform_get_secret for plaintext",
            }
        if item.value_type == "file":
            return {
                "key": key,
                "value_type": "file",
                "path": item.file_path,
                "filename": Path(item.file_path).name if item.file_path else None,
                "size": item.file_size,
                "mime": item.file_mime,
                "description": item.description,
            }
        if item.value_type == "dataset":
            return {
                "key": key,
                "value_type": "dataset",
                "preview_count": (
                    len(item.value_json)
                    if isinstance(item.value_json, list)
                    else (1 if isinstance(item.value_json, dict) else 0)
                ),
                "description": item.description,
            }
        return {
            "key": key,
            "value_type": item.value_type,
            "value": item.template_substitution_value(),
            "description": item.description,
            "synthetic": item.synthetic_source is not None,
            "synthetic_source": item.synthetic_source,
        }

    async def _get_secret(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        if not key:
            return {"error": "key required"}
        item = resolver.data.get(key)
        if item is None or item.value_type != "secret":
            return {"error": f"secret '{key}' not found or wrong type"}
        try:
            plain = item.resolve_secret()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {"key": key, "value": plain, "_test_data_secret_used": True}

    async def _get_file(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        if not key:
            return {"error": "key required"}
        item = resolver.data.get(key)
        if item is None or item.value_type != "file":
            return {"error": f"file '{key}' not found or wrong type"}
        return {
            "key": key,
            "path": item.file_path,
            "filename": Path(item.file_path).name if item.file_path else None,
            "size": item.file_size,
            "mime": item.file_mime,
        }

    async def _iter_dataset(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        if not key:
            return {"error": "key required"}
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 20)
        item = resolver.data.get(key)
        if item is None or item.value_type != "dataset":
            return {"error": f"dataset '{key}' not found or wrong type"}
        slice_rows, total = _dataset_slice(item.value_json, max(0, offset), max(1, min(limit, 200)))
        return {"key": key, "total": total, "offset": offset, "items": slice_rows}

    async def _synthesize(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        if not key:
            return {"error": "key required"}
        hint = str(args.get("hint") or "")
        value_type = str(args.get("value_type") or "string")

        existing = resolver.data.get(key)
        if existing is not None and existing.synthetic_source is None:
            return {
                "key": key,
                "value": existing.template_substitution_value(),
                "source": "existing_material",
                "note": "已存在配置物料，请直接使用 platform_get_test_data / 模板替换后的值",
            }
        if existing is not None and existing.synthetic_source is not None:
            return {
                "key": key,
                "value": existing.value_text or "",
                "source": existing.synthetic_source or "cached_synthetic",
                "warning": "reused_cached_synthetic",
            }

        sv = await synth.synthesize(key, hint, value_type)
        hint_tag = hint.strip() or None
        resolver.cache_synthesized(key, sv.value, sv.source, hint=hint_tag)
        return {
            "key": key,
            "value": sv.value,
            "source": sv.source,
            "warning": "AI synthesized — accuracy not guaranteed",
        }

    async def _mark_failure(args: dict[str, Any]) -> dict[str, Any]:
        key = (args.get("key") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not key:
            return {"error": "key required"}
        if not reason:
            return {"error": "reason required"}
        resolver.current_case_mark_data_failure(key, reason)
        return {"acknowledged": True, "case_will_be_marked": "data_failure"}

    factories = (
        _get_test_data,
        _get_secret,
        _get_file,
        _iter_dataset,
        _synthesize,
        _mark_failure,
    )
    for suffix, fn in zip(_PLATFORM_SUFFIXES, factories, strict=True):
        full = _tool_name(ns, suffix)
        register_tool(full, fn)
        registered.append(full)
    return registered


def unregister_data_tools(execution_id: uuid.UUID | str) -> int:
    """仅卸载 data platform 六个工具；不影响同前缀下的 browser_* / captcha 等。"""
    ns = str(execution_id)
    removed = 0
    for suffix in _PLATFORM_SUFFIXES:
        if unregister_tool(_tool_name(ns, suffix)):
            removed += 1
    return removed


__all__ = [
    "platform_tools_openai_schemas",
    "redact_tool_result_for_reasoning",
    "register_data_tools",
    "unregister_data_tools",
]
