"""Task 9.6 — ``execution_service.py`` 单测。

策略：
- 不连真 PG / 真 Engine。``persistence`` 用 monkeypatch 桩；``ExecutionEngine``
  替成 no-op AsyncMock；``EXECUTION_STREAM_HUB.register`` 也用真实的（in-memory
  hub 本身就是测试友好的）。
- 重点验证：
  1. ``start_execution`` 走完"权限校验 → 写 pending 行 → register hub →
     派 task → 返回 list item"五步
  2. ``stop_execution`` 幂等：终态 → already_terminal=True
  3. ``retry_failed_execution`` 抽出失败用例 + 复用 config_snapshot
  4. ``_resolve_environment_id`` 三种分支：传了 / 没传 / 项目无环境
  5. ``_validate_testcase_ownership`` 防 IDOR
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException, NotFoundException
from app.modules.ui_automation import execution_service
from app.modules.ui_automation.schemas import (
    ExecutionCreateRequest,
    ExecutionRetryRequest,
)

# ─── DB / persistence 桩 ──────────────────────────────────────────────


class _DBStub:
    """最小 DB session 桩：execute() 返回 ``_ResultStub``；get() 由 setup 注入。"""

    def __init__(self):
        # (model, id) -> instance
        self.objects: dict[tuple[type, uuid.UUID], object] = {}
        # 每次 execute 调用按顺序消费 ``execute_results``；不够则报 RuntimeError
        self.execute_results: list = []
        self.flushed = False

    async def get(self, model, id_):
        return self.objects.get((model, id_))

    async def execute(self, _stmt):
        if not self.execute_results:
            return _ResultStub(scalar=None, scalar_list=[])
        return self.execute_results.pop(0)

    async def flush(self):
        self.flushed = True


class _ResultStub:
    def __init__(self, *, scalar=None, scalar_list=None):
        self._scalar = scalar
        self._scalar_list = scalar_list or []

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _ScalarsStub(self._scalar_list)


class _ScalarsStub:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def unique(self):
        return self


def _make_user(*, is_superuser: bool = True) -> SimpleNamespace:
    """构造一个最小 user 桩：服务层 ``_check_project_member`` 看 is_superuser
    免去成员表校验，简化测试。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_superuser=is_superuser,
        username="tester",
    )


def _make_env_row(*, project_id: uuid.UUID, env_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=env_id or uuid.uuid4(),
        project_id=project_id,
        name="dev",
        token_budget=10_000,
    )


def _make_execution_row(
    *,
    project_id: uuid.UUID,
    status: str = "pending",
    case_results: list | None = None,
    config_snapshot: dict | None = None,
):
    eid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=eid,
        project_id=project_id,
        environment_id=uuid.uuid4(),
        status=status,
        mode="normal",
        total_cases=2,
        passed_cases=0,
        failed_cases=0,
        skipped_cases=0,
        duration_ms=None,
        tokens_total=0,
        video_path=None,
        trace_path=None,
        chat_message_id=None,
        started_at=None,
        completed_at=None,
        triggered_by=None,
        test_data_snapshot=None,
        config_snapshot=config_snapshot or {},
        error_message=None,
        case_results=case_results or [],
        created_at=now,
        updated_at=now,
    )


# ─── start_execution ──────────────────────────────────────────────────


@pytest.fixture
def patch_engine_and_persistence(monkeypatch):
    """统一 patch：persistence 写库 → 桩；ExecutionEngine.run → AsyncMock。"""
    inits: list[dict] = []

    async def fake_init(**kw):
        inits.append(kw)
        return SimpleNamespace(id=kw["execution_id"])

    monkeypatch.setattr(
        execution_service.persistence, "init_execution_record", fake_init,
    )

    engine_runs: list = []

    async def fake_run(self, inputs):
        engine_runs.append(inputs)
        return SimpleNamespace(execution_id=inputs.execution_id, status="completed")

    monkeypatch.setattr(execution_service.ExecutionEngine, "run", fake_run)

    # 阻止 service 真去校验 project member（依赖完整 DB）
    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    async def noop_ensure_project(_db, _pid):
        return None

    monkeypatch.setattr(execution_service, "_ensure_project_exists", noop_ensure_project)

    return SimpleNamespace(inits=inits, engine_runs=engine_runs)


@pytest.mark.asyncio
async def test_start_execution_happy_path(monkeypatch, patch_engine_and_persistence):
    project_id = uuid.uuid4()
    env_id = uuid.uuid4()
    tc_ids = [uuid.uuid4(), uuid.uuid4()]
    user = _make_user()

    db = _DBStub()
    env_row = _make_env_row(project_id=project_id, env_id=env_id)
    db.objects[(execution_service.TestEnvironment, env_id)] = env_row

    # _validate_testcase_ownership: select 返回所有 tc id
    db.execute_results.append(_ResultStub(scalar_list=list(tc_ids)))
    # start_execution 末尾再 db.get(UIExecution, ...) — 注入返回值
    # 我们在 init_execution_record 里没有真写 DB，所以这里用 patch 让 db.get
    # 返回一个新拼的行
    last_row_id_holder: dict = {}

    real_get = db.get

    async def patched_get(model, id_):
        if model is execution_service.UIExecution:
            row = _make_execution_row(project_id=project_id)
            row.id = id_
            last_row_id_holder["id"] = id_
            return row
        return await real_get(model, id_)

    db.get = patched_get  # type: ignore[method-assign]

    req = ExecutionCreateRequest(
        testcase_ids=tc_ids,
        environment_id=env_id,
        mode="normal",
        loaded_set_ids=[],
        manual_overrides={"username": "alice"},
        token_budget=20_000,
        strict_data_mode=False,
    )

    item = await execution_service.start_execution(db, project_id, req, user)

    # 1) persistence.init_execution_record 被调用一次，参数完整
    assert len(patch_engine_and_persistence.inits) == 1
    init_args = patch_engine_and_persistence.inits[0]
    assert init_args["project_id"] == project_id
    assert init_args["environment_id"] == env_id
    assert init_args["mode"] == "normal"
    assert init_args["total_cases"] == 2
    snapshot = init_args["config_snapshot"]
    assert snapshot["loaded_set_ids"] == []
    assert snapshot["manual_overrides"] == {"username": "alice"}
    assert snapshot["token_budget_override"] == 20_000

    # 2) 返回 list item
    assert item.project_id == project_id
    assert item.id == last_row_id_holder["id"]

    # 3) Engine 后台任务被派发（asyncio.create_task 立刻 schedule，但因为我们
    #    await item 时事件循环已 yield 一次，task 应已开始）。这里给一个 yield
    #    机会让 task 跑到我们的 fake_run。
    import asyncio
    await asyncio.sleep(0)
    assert len(patch_engine_and_persistence.engine_runs) == 1
    inputs = patch_engine_and_persistence.engine_runs[0]
    assert inputs.execution_id == item.id
    assert inputs.testcase_ids == tc_ids
    assert inputs.manual_overrides == {"username": "alice"}


@pytest.mark.asyncio
async def test_start_execution_rejects_cross_project_testcases(
    monkeypatch, patch_engine_and_persistence,
):
    """传入的 testcase_ids 中有 id 不属于 project_id → 400。"""
    project_id = uuid.uuid4()
    env_id = uuid.uuid4()
    tc_a = uuid.uuid4()
    tc_b = uuid.uuid4()  # 属于别的项目，不应在结果里
    user = _make_user()

    db = _DBStub()
    db.objects[(execution_service.TestEnvironment, env_id)] = _make_env_row(
        project_id=project_id, env_id=env_id,
    )
    # 模拟 _validate_testcase_ownership：只返回 tc_a
    db.execute_results.append(_ResultStub(scalar_list=[tc_a]))

    req = ExecutionCreateRequest(
        testcase_ids=[tc_a, tc_b], environment_id=env_id,
    )
    with pytest.raises(AppException) as excinfo:
        await execution_service.start_execution(db, project_id, req, user)
    assert excinfo.value.code == "TESTCASE_NOT_IN_PROJECT"
    # Engine 不应被派发
    assert patch_engine_and_persistence.engine_runs == []


@pytest.mark.asyncio
async def test_start_execution_no_environment_in_project(
    monkeypatch, patch_engine_and_persistence,
):
    project_id = uuid.uuid4()
    user = _make_user()
    db = _DBStub()
    # _resolve_environment_id 第一次 execute 取项目最新 env，返回 None
    db.execute_results.append(_ResultStub(scalar=None))

    req = ExecutionCreateRequest(testcase_ids=[uuid.uuid4()])
    with pytest.raises(AppException) as excinfo:
        await execution_service.start_execution(db, project_id, req, user)
    assert excinfo.value.code == "NO_ENVIRONMENT"


@pytest.mark.asyncio
async def test_start_execution_explicit_env_wrong_project(
    monkeypatch, patch_engine_and_persistence,
):
    project_id = uuid.uuid4()
    env_id = uuid.uuid4()
    user = _make_user()
    db = _DBStub()
    # env 存在但属于别的 project
    db.objects[(execution_service.TestEnvironment, env_id)] = _make_env_row(
        project_id=uuid.uuid4(),  # 不同项目
        env_id=env_id,
    )

    req = ExecutionCreateRequest(testcase_ids=[uuid.uuid4()], environment_id=env_id)
    with pytest.raises(NotFoundException):
        await execution_service.start_execution(db, project_id, req, user)


# ─── stop_execution ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_execution_running(monkeypatch):
    project_id = uuid.uuid4()
    user = _make_user()
    row = _make_execution_row(project_id=project_id, status="running")

    db = _DBStub()
    db.objects[(execution_service.UIExecution, row.id)] = row

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    result = await execution_service.stop_execution(db, row.id, user)
    assert result.status == "stopped"
    assert result.already_terminal is False
    # row 的 status 已被修改
    assert row.status == "stopped"
    assert db.flushed is True


@pytest.mark.asyncio
async def test_stop_execution_idempotent_terminal(monkeypatch):
    project_id = uuid.uuid4()
    user = _make_user()
    row = _make_execution_row(project_id=project_id, status="completed")

    db = _DBStub()
    db.objects[(execution_service.UIExecution, row.id)] = row

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    result = await execution_service.stop_execution(db, row.id, user)
    assert result.status == "completed"
    assert result.already_terminal is True
    assert row.status == "completed"  # 未被改写


@pytest.mark.asyncio
async def test_stop_execution_not_found(monkeypatch):
    user = _make_user()
    db = _DBStub()
    with pytest.raises(NotFoundException):
        await execution_service.stop_execution(db, uuid.uuid4(), user)


# ─── delete_execution ────────────────────────────────────────────────


class _DBDeleteStub(_DBStub):
    """``delete_execution`` 用：补 ``delete()`` + ``execute()`` 多次调用支持。"""

    def __init__(self):
        super().__init__()
        self.deleted_objects: list = []

    async def delete(self, obj):
        self.deleted_objects.append(obj)


@pytest.mark.asyncio
async def test_delete_execution_terminal_purges_files_and_row(
    monkeypatch, tmp_path,
):
    """**关键回归**：终态执行 → 删 DB 行 + ``safe_unlink`` 所有 artifact 文件。
    业务规则：删 execution 后 video / trace / step screenshot 不能"孤悬磁盘"，
    因为 ``cleanup_old_media`` 是按 ``completed_at < cutoff`` 扫的，删 DB 后再
    也扫不到这些文件。"""
    project_id = uuid.uuid4()
    user = _make_user()
    row = _make_execution_row(project_id=project_id, status="completed")
    # 造两个 artifact 文件（video + screenshot），让 safe_unlink 能真删
    video = tmp_path / "video.webm"
    video.write_bytes(b"\x00")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x00")
    row.video_path = str(video)
    row.trace_path = None  # 故意留空 → 验证 None 被跳过

    db = _DBDeleteStub()
    db.objects[(execution_service.UIExecution, row.id)] = row
    # screenshot 路径查询的返回
    db.execute_results.append(_ResultStub(scalar_list=[str(shot), None, ""]))

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    result = await execution_service.delete_execution(db, row.id, user)

    assert result["deleted"] is True
    assert result["files_deleted"] == 2  # video + shot；None / "" 被跳过
    assert row in db.deleted_objects, "DB 行必须被 delete()"
    assert not video.exists(), "video 文件必须被 unlink"
    assert not shot.exists(), "screenshot 文件必须被 unlink"


@pytest.mark.asyncio
async def test_delete_execution_rejects_non_terminal(monkeypatch):
    """**关键安全检查**：running / pending 不允许删除——Engine 还在写文件，
    现在删会 race。返回 409 让前端引导用户先点"停止"。"""
    project_id = uuid.uuid4()
    user = _make_user()
    row = _make_execution_row(project_id=project_id, status="running")
    db = _DBDeleteStub()
    db.objects[(execution_service.UIExecution, row.id)] = row

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    with pytest.raises(AppException) as exc_info:
        await execution_service.delete_execution(db, row.id, user)
    assert exc_info.value.status_code == 409
    assert "终态" in exc_info.value.message
    # 没删 DB 也没 unlink
    assert row not in db.deleted_objects


@pytest.mark.asyncio
async def test_delete_execution_not_found(monkeypatch):
    user = _make_user()
    db = _DBDeleteStub()
    with pytest.raises(NotFoundException):
        await execution_service.delete_execution(db, uuid.uuid4(), user)


@pytest.mark.asyncio
async def test_delete_execution_handles_missing_files_gracefully(
    monkeypatch, tmp_path,
):
    """artifact 路径写在 DB 但磁盘文件不存在（已被 cleanup 任务清掉）—— 删
    execution 不能因此报错；``safe_unlink`` 静默跳过。"""
    project_id = uuid.uuid4()
    user = _make_user()
    row = _make_execution_row(project_id=project_id, status="completed")
    row.video_path = str(tmp_path / "ghost.webm")  # 文件不存在

    db = _DBDeleteStub()
    db.objects[(execution_service.UIExecution, row.id)] = row
    db.execute_results.append(_ResultStub(scalar_list=[]))

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    result = await execution_service.delete_execution(db, row.id, user)
    assert result["deleted"] is True
    assert result["files_deleted"] == 0  # 文件本来就不存在，零删除是正常


# ─── retry_failed_execution ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_failed_execution_filters_failed_cases(
    monkeypatch, patch_engine_and_persistence,
):
    project_id = uuid.uuid4()
    user = _make_user()
    env_id = uuid.uuid4()
    tc_pass = uuid.uuid4()
    tc_fail = uuid.uuid4()
    tc_err = uuid.uuid4()

    case_results = [
        SimpleNamespace(testcase_id=tc_pass, status="passed"),
        SimpleNamespace(testcase_id=tc_fail, status="failed"),
        SimpleNamespace(testcase_id=tc_err, status="error"),
    ]
    row = _make_execution_row(
        project_id=project_id,
        status="completed",
        case_results=case_results,
        config_snapshot={
            "loaded_set_ids": [],
            "manual_overrides": {"existing": "value"},
            "llm_config_id": None,
            "token_budget_override": 30_000,
            "strict_data_mode": True,
        },
    )
    row.environment_id = env_id

    db = _DBStub()
    # retry_failed_execution 第一次 execute 拿带 case_results 的行
    db.execute_results.append(_ResultStub(scalar=row))
    # _resolve_environment_id：环境对象（user 传 env_id 走 db.get 分支）
    db.objects[(execution_service.TestEnvironment, env_id)] = _make_env_row(
        project_id=project_id, env_id=env_id,
    )
    # _validate_testcase_ownership：返回 fail / err id
    db.execute_results.append(_ResultStub(scalar_list=[tc_fail, tc_err]))

    # 末尾 db.get(UIExecution, new_id)：返回新 row
    real_get = db.get

    async def patched_get(model, id_):
        if model is execution_service.UIExecution:
            new_row = _make_execution_row(project_id=project_id)
            new_row.id = id_
            return new_row
        return await real_get(model, id_)

    db.get = patched_get  # type: ignore[method-assign]

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    item = await execution_service.retry_failed_execution(
        db, row.id, ExecutionRetryRequest(extra_manual_overrides={"new_key": "v"}), user,
    )

    # 新 execution 已被 init
    assert len(patch_engine_and_persistence.inits) == 1
    init = patch_engine_and_persistence.inits[0]
    snapshot = init["config_snapshot"]
    # 只跑失败/错误用例，且去掉 passed
    assert set(snapshot["testcase_ids"]) == {str(tc_fail), str(tc_err)}
    # manual_overrides 合并：原 existing + 新 new_key
    assert snapshot["manual_overrides"] == {"existing": "value", "new_key": "v"}
    # strict_data_mode 沿用原值
    assert snapshot["strict_data_mode"] is True
    # token_budget 沿用原值
    assert snapshot["token_budget_override"] == 30_000
    assert item.id != row.id  # 新 execution


@pytest.mark.asyncio
async def test_retry_failed_no_failed_cases_raises(monkeypatch, patch_engine_and_persistence):
    project_id = uuid.uuid4()
    user = _make_user()
    case_results = [
        SimpleNamespace(testcase_id=uuid.uuid4(), status="passed"),
        SimpleNamespace(testcase_id=uuid.uuid4(), status="passed"),
    ]
    row = _make_execution_row(
        project_id=project_id, status="completed", case_results=case_results,
    )
    db = _DBStub()
    db.execute_results.append(_ResultStub(scalar=row))

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)

    with pytest.raises(AppException) as excinfo:
        await execution_service.retry_failed_execution(
            db, row.id, ExecutionRetryRequest(), user,
        )
    assert excinfo.value.code == "NO_FAILED_CASES"


# ─── get_recent_config（Task 10.1）────────────────────────────────────


@pytest.mark.asyncio
async def test_recent_config_returns_none_when_no_history(monkeypatch) -> None:
    """从未跑过 → None；前端"复用上次"按钮置灰。"""
    user = _make_user()
    db = _DBStub()

    class _EmptyRowsResult:
        def all(self):
            return []

    db.execute_results.append(_EmptyRowsResult())

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)
    monkeypatch.setattr(
        execution_service, "_ensure_project_exists",
        lambda *_a, **_k: _aio_none(),
    )

    cfg = await execution_service.get_recent_config(
        db, uuid.uuid4(), user, testcase_ids=[uuid.uuid4()],
    )
    assert cfg is None


@pytest.mark.asyncio
async def test_recent_config_exact_match_wins_over_recency(monkeypatch) -> None:
    """两条历史：最近的 testcase_ids 不匹配，倒数第二条匹配 → 应返回匹配那条
    的 config（精确组合优先于纯按时间）。"""
    user = _make_user()
    pid = uuid.uuid4()
    tc_a = uuid.uuid4()
    tc_b = uuid.uuid4()

    matched_cfg = {
        "testcase_ids": [str(tc_a), str(tc_b)],
        "loaded_set_ids": ["set-1"],
        "manual_overrides": {"captcha": "0000"},
        "mode": "normal",
        "token_budget_override": 80_000,
    }
    other_cfg = {
        "testcase_ids": [str(uuid.uuid4())],
        "loaded_set_ids": [],
        "manual_overrides": {},
        "mode": "normal",
        "token_budget_override": None,
    }

    db = _DBStub()
    # 模拟 .all() 返回的元组 (config_snapshot, environment_id)
    env_id = uuid.uuid4()

    class _RowsResult:
        def all(self):
            # 倒序 created_at：最近的 other 在前
            return [(other_cfg, env_id), (matched_cfg, env_id)]

    db.execute_results.append(_RowsResult())

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)
    monkeypatch.setattr(
        execution_service, "_ensure_project_exists",
        lambda *_a, **_k: _aio_none(),
    )

    cfg = await execution_service.get_recent_config(
        db, pid, user, testcase_ids=[tc_b, tc_a],  # 顺序无关
    )
    assert cfg is not None
    assert set(cfg["testcase_ids"]) == {str(tc_a), str(tc_b)}
    assert cfg["manual_overrides"] == {"captcha": "0000"}
    assert cfg["environment_id"] == str(env_id)


@pytest.mark.asyncio
async def test_recent_config_falls_back_to_latest_when_no_match(monkeypatch) -> None:
    """完全没匹配的 testcase_ids → 退一步给最近一次任意配置（让用户继承
    自己的 LLM / token / mode 偏好）。"""
    user = _make_user()
    pid = uuid.uuid4()
    env_id = uuid.uuid4()

    other_cfg = {
        "testcase_ids": [str(uuid.uuid4())],
        "loaded_set_ids": [],
        "manual_overrides": {},
        "mode": "debug",
        "token_budget_override": 50_000,
    }
    db = _DBStub()

    class _RowsResult:
        def all(self):
            return [(other_cfg, env_id)]

    db.execute_results.append(_RowsResult())

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)
    monkeypatch.setattr(
        execution_service, "_ensure_project_exists",
        lambda *_a, **_k: _aio_none(),
    )

    cfg = await execution_service.get_recent_config(
        db, pid, user, testcase_ids=[uuid.uuid4()],
    )
    assert cfg is not None
    assert cfg["mode"] == "debug"
    assert cfg["token_budget_override"] == 50_000


@pytest.mark.asyncio
async def test_recent_config_no_testcase_ids_returns_latest(monkeypatch) -> None:
    """不传 testcase_ids → 返回最近一次配置（任意用例组合）。"""
    user = _make_user()
    pid = uuid.uuid4()
    env_id = uuid.uuid4()

    cfg_a = {
        "testcase_ids": [str(uuid.uuid4())],
        "loaded_set_ids": [],
        "manual_overrides": {},
        "mode": "normal",
        "token_budget_override": None,
    }
    db = _DBStub()

    class _RowsResult:
        def all(self):
            return [(cfg_a, env_id)]

    db.execute_results.append(_RowsResult())

    async def noop_member(_db, _pid, _user):
        return None

    monkeypatch.setattr(execution_service, "_check_project_member", noop_member)
    monkeypatch.setattr(
        execution_service, "_ensure_project_exists",
        lambda *_a, **_k: _aio_none(),
    )

    cfg = await execution_service.get_recent_config(
        db, pid, user, testcase_ids=None,
    )
    assert cfg is not None
    assert cfg["environment_id"] == str(env_id)


async def _aio_none():
    return None


# ─── 辅助 helper ──────────────────────────────────────────────────────


def test_maybe_uuid_coerces_str() -> None:
    u = uuid.uuid4()
    assert execution_service._maybe_uuid(str(u)) == u
    assert execution_service._maybe_uuid(u) == u
    assert execution_service._maybe_uuid(None) is None
    assert execution_service._maybe_uuid("not-a-uuid") is None
    assert execution_service._maybe_uuid(123) is None


def test_to_case_response_carries_testcase_business_no() -> None:
    """``_to_case_response`` 应把 testcase 的业务编号 ``case_no`` 透传到
    ``ExecutionCaseResponse.testcase_no`` —— 这是测试报告里渲染 ``TC-0117``
    格式人话编号的来源。

    覆盖三种典型 case：
    1. testcase 存在 + 给了 meta（最常见）
    2. testcase 存在但 meta 里没记录（用例已删除导致 join 缺）
    3. testcase_id=None（手动一次性执行，没绑用例）
    """
    case_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    tc_id = uuid.uuid4()
    module_id = uuid.uuid4()

    case_with_meta = SimpleNamespace(
        id=case_id, execution_id=exec_id, testcase_id=tc_id,
        status="passed", error_message=None, ai_summary=None,
        duration_ms=1234, tokens_used=100, sort_order=0,
        test_data_used=None, synthesized_data=[], data_failures=[],
        data_confidence="reliable",
        started_at=None, completed_at=None,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        step_results=[],
    )
    meta_map = {
        tc_id: {
            "case_no": 117,
            "title": "搜索北京天气",
            "module_id": module_id,
            "module_name": "百度一下测试 demo",
        },
    }
    out = execution_service._to_case_response(case_with_meta, meta_map)
    assert out.testcase_no == 117
    assert out.testcase_title == "搜索北京天气"
    assert out.testcase_module_id == module_id
    assert out.testcase_module_name == "百度一下测试 demo"

    out_no_meta = execution_service._to_case_response(case_with_meta, {})
    assert out_no_meta.testcase_no is None
    assert out_no_meta.testcase_title is None

    case_orphan = SimpleNamespace(
        **{**case_with_meta.__dict__, "testcase_id": None},
    )
    out_orphan = execution_service._to_case_response(case_orphan, meta_map)
    assert out_orphan.testcase_no is None
    assert out_orphan.testcase_id is None


# ─── _artifact_path_to_url / video_url 字段（修复"视频加载失败"故障）──


def _patch_artifacts_root(monkeypatch, root: str) -> None:
    """``_artifact_path_to_url`` 内部 ``from app.config import settings`` 后才用
    ``settings.UI_ARTIFACTS_DIR``，所以测试必须 patch 到 ``app.config.settings``
    模块属性上。"""
    from app.config import settings as cfg_settings

    monkeypatch.setattr(cfg_settings, "UI_ARTIFACTS_DIR", root, raising=False)


def test_artifact_path_to_url_for_artifact_inside_root(monkeypatch, tmp_path) -> None:
    """``ui_artifacts`` 目录下的绝对路径必须被转成 ``/uploads/ui_artifacts/<rel>``
    形式的 nginx 静态路径——前端 ``<video src>`` / ``<img src>`` 直接用，绕开
    后端鉴权（HTML media 元素发请求不带 Authorization header，鉴权 API 必 401）。
    """
    _patch_artifacts_root(monkeypatch, str(tmp_path))
    abs_path = str(tmp_path / "exec-id" / "video" / "page@xxx.webm")
    url = execution_service._artifact_path_to_url(abs_path)
    assert url == "/uploads/ui_artifacts/exec-id/video/page@xxx.webm"


def test_artifact_path_to_url_returns_none_for_path_outside_root(
    monkeypatch, tmp_path,
) -> None:
    """artifact 根之外的路径（典型不会发生，但万一 DB 里有脏数据）必须返回
    None，避免返回 ``/uploads/ui_artifacts/../../etc/passwd`` 这种穿越路径。"""
    _patch_artifacts_root(monkeypatch, str(tmp_path / "artifacts"))
    foreign = str(tmp_path / "elsewhere" / "x.webm")
    assert execution_service._artifact_path_to_url(foreign) is None


def test_artifact_path_to_url_handles_empty_input() -> None:
    assert execution_service._artifact_path_to_url(None) is None
    assert execution_service._artifact_path_to_url("") is None


def test_to_execution_detail_emits_video_and_trace_url(monkeypatch, tmp_path) -> None:
    """**关键回归**：``ExecutionDetailResponse`` 必须返回 ``video_url`` /
    ``trace_url`` 字段——前端 ``<video src>`` 必须用 nginx 静态路径，否则
    会 401 加载失败（实际故障：测试报告"视频加载失败"）。"""
    _patch_artifacts_root(monkeypatch, str(tmp_path))
    pid = uuid.uuid4()
    row = _make_execution_row(project_id=pid, status="completed")
    eid = row.id
    row.video_path = str(tmp_path / str(eid) / "video" / "page@abc.webm")
    row.trace_path = str(tmp_path / str(eid) / "trace.zip")

    out = execution_service._to_detail(row, effective_token_budget=10_000)
    assert out.video_url == f"/uploads/ui_artifacts/{eid}/video/page@abc.webm"
    assert out.trace_url == f"/uploads/ui_artifacts/{eid}/trace.zip"
    # 旧字段也保留（运维 / 排错用）
    assert out.video_path == row.video_path
    assert out.trace_path == row.trace_path
    assert out.has_video is True
    assert out.has_trace is True


def test_to_execution_detail_video_url_is_none_when_no_recording(
    monkeypatch, tmp_path,
) -> None:
    """``video_path`` 为 None 时 ``video_url`` 也应是 None，让前端隐藏播放器。"""
    _patch_artifacts_root(monkeypatch, str(tmp_path))
    row = _make_execution_row(project_id=uuid.uuid4(), status="completed")
    out = execution_service._to_detail(row, effective_token_budget=10_000)
    assert out.video_url is None
    assert out.trace_url is None
    assert out.has_video is False
