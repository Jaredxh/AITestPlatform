"""StorageState 文件治理 — Task 8.1 提供，Task 8.2 / 9.x 调用。

Playwright 的 BrowserContext 可以导出 ``storage_state`` JSON（含 cookies +
localStorage + sessionStorage 等"用户已登录"的全部信息），后续启动新
context 时通过 ``storage_state=path`` 直接载入，无需重新走登录流程。

本模块只负责"路径计算 + 文件存在判断 + 失效"三件纯文件系统操作；真正
**写** state 由 Task 8.2 ``PreconditionRunner`` 完成（它握着 BrowserContext，
能调 ``await context.storage_state(path=...)``）。

这样 Task 8.1 不需要 import playwright 就能完成 state CRUD，单测也无需 mock
浏览器。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _state_root() -> Path:
    """state 根目录的惰性解析。

    用函数而非模块级常量是为了让测试能 ``monkeypatch settings.UI_STATE_DIR``
    后立刻生效，不受首次 import 时刻"冻结"。
    """
    return Path(settings.UI_STATE_DIR)


def state_path_for(environment_id: uuid.UUID, *, session_name: str | None = None) -> Path:
    """根据 environment 计算 storage_state 文件路径。

    命名策略（与 ``models.TestEnvironment.session_name`` 字段联动）：
    - ``session_name`` 非空 → ``<root>/session_<session_name>.json``
      多个环境共享同一份登录态（同账号跑 dev / staging）
    - 否则 → ``<root>/env_<environment_id>.json`` 一环境一份

    返回 Path，**不**确认文件是否存在；调用方按需 ``load_state_or_none``。
    """
    if session_name and session_name.strip():
        # session 名字进文件名前清洗一下，避免 ``/`` / `..` 等路径注入
        safe_name = _sanitize_filename_segment(session_name.strip())
        return _state_root() / f"session_{safe_name}.json"
    return _state_root() / f"env_{environment_id}.json"


def load_state_or_none(
    environment_id: uuid.UUID,
    *,
    session_name: str | None = None,
) -> str | None:
    """已存在 state 文件 → 返回字符串路径（playwright 直接接收）；否则 None。

    返回 str（而非 Path）的原因：``BrowserContext`` 的 ``storage_state`` 参数
    类型签名是 ``str | Path | dict | None``，但 Path 在某些 playwright 版本
    序列化有 quirk，str 最稳。
    """
    path = state_path_for(environment_id, session_name=session_name)
    if path.exists() and path.is_file():
        return str(path)
    return None


async def mark_state_stale(
    environment_id: uuid.UUID,
    *,
    session_name: str | None = None,
    db_clear_callback=None,
) -> tuple[bool, bool]:
    """让某个环境的 state 失效。

    返回 ``(file_existed, file_removed)`` 元组：
    - ``file_existed=True / file_removed=True``：成功删除
    - ``file_existed=True / file_removed=False``：文件存在但删除失败（权限 / 锁）
    - ``file_existed=False / file_removed=False``：本来就没文件

    DB 字段（``state_saved_at = None``）的清理由 ``db_clear_callback`` 传入
    实现 —— 这样本模块依旧不依赖 service / model，纯文件系统职责。

    ``async`` 是为了让 callback 能是 coroutine（service 层会传一个 await
    db.commit() 的）；本身的 fs 操作是同步的。
    """
    path = state_path_for(environment_id, session_name=session_name)
    file_existed = path.exists()
    file_removed = False
    if file_existed:
        try:
            path.unlink()
            file_removed = True
        except OSError as exc:
            logger.warning(
                "Failed to remove state file %s for env %s: %s",
                path, environment_id, exc,
            )
    if db_clear_callback is not None:
        await db_clear_callback()
    return file_existed, file_removed


def ensure_state_dir() -> Path:
    """确保 state 根目录存在并可写；返回根目录 Path。

    应用启动时 / 第一次写 state 前调一次即可（如 main.startup 或 service
    层第一次操作）。本 task 不强行在 startup 调，留给 Task 8.2 写 state 时
    自行 ``ensure``。
    """
    root = _state_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_filename_segment(name: str) -> str:
    """把任意字符串转成安全的文件名片段。

    去掉路径分隔符、控制字符、起始 ``.``（防 hidden file），把剩余的非
    字母数字 / 下划线 / 连字符替换成 ``_``。
    """
    safe = []
    for ch in name:
        if ch.isalnum() or ch in ("_", "-"):
            safe.append(ch)
        else:
            safe.append("_")
    cleaned = "".join(safe).strip("._")
    if not cleaned:
        cleaned = "default"
    # 防止文件名过长（255 是大多数 fs 上限，留点余量给前后缀）
    return cleaned[:120]


__all__ = [
    "state_path_for",
    "load_state_or_none",
    "mark_state_stale",
    "ensure_state_dir",
]
