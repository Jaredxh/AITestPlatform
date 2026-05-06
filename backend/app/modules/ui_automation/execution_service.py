"""执行 API 的业务逻辑层（Task 9.6）。

这一层把 ``ExecutionEngine``（同步内部 API）包装成"HTTP 友好"的形态：

- ``start_execution``：写一行 ``ui_executions`` (status=pending)，先 register
  ``EXECUTION_STREAM_HUB``，再 ``asyncio.create_task`` 派 Engine 后台跑——
  请求线程立即返回 ``execution_id``，前端拿着去 SSE 订阅。
- ``list_executions / get_execution_detail``：查表，做权限校验后 schema 转换。
- ``stop_execution``：把 status 改成 ``stopped``；Engine 在循环间隙轮询
  ``persistence.is_execution_stopped`` 自然中止（与 Engine 实现对齐）。幂等。
- ``retry_failed_execution``：从原 execution 抽出失败/错误/跳过的用例 +
  复用原 ``config_snapshot``，再走 ``start_execution`` 派一次。

权限：与 router 同步用 ``UI_EXEC_*`` 系列；本层只做"项目成员可见性"二次校验
（阻止 IDOR）。SSE 端点单独走 ``ui_exec:view``，stop 走 ``ui_exec:stop``，
start 与 retry 走 ``ui_exec:run``。

为什么"start 写 pending 行 + 立即返回"而不是同步等 Engine？
- Engine 一次跑可能几十分钟（多用例 + LLM 思考），HTTP 不能 hang 那么久。
- 与一期 chat / testcase generation 已落地的"在线 stream hub + 后台 task"
  架构对齐，前端只需要复用一套 SSE 重连机制就能看进度。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException
from app.modules.auth.models import User
from app.modules.ui_automation import persistence
from app.modules.ui_automation.debug_control import DEBUG_CONTROL_HUB
from app.modules.ui_automation.execution_engine import (
    ExecutionEngine,
    ExecutionInputs,
)
from app.modules.ui_automation.models import (
    TestEnvironment,
    UICaseResult,
    UIExecution,
    UIStepResult,
)
from app.modules.ui_automation.schemas import (
    ExecutionCaseResponse,
    ExecutionContinueResponse,
    ExecutionCreateRequest,
    ExecutionDetailResponse,
    ExecutionListItem,
    ExecutionRetryRequest,
    ExecutionStepResponse,
    ExecutionStopResponse,
    PreflightModuleItem,
    PreflightModulesRequest,
    PreflightModulesResponse,
)
from app.modules.ui_automation.service import _check_project_member, _ensure_project_exists
from app.modules.ui_automation.stream_hub import EXECUTION_STREAM_HUB

logger = logging.getLogger(__name__)


# 与 ``models.EXECUTION_STATUSES`` 对齐：终态集合。
# 拎成常量是因为 stop / retry / SSE 三处都要判同一组。
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "stopped", "failed", "aborted_budget"},
)


# ─── 启动执行 ────────────────────────────────────────────────────────


async def start_execution(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: ExecutionCreateRequest,
    user: User,
) -> ExecutionListItem:
    """创建 execution 行 → 派 ``ExecutionEngine`` 后台跑 → 立即返回行。

    步骤：
    1. 验项目成员 + 解析 environment（不传 → 取项目最新一个；都没有 → 400）
    2. 检查 ``testcase_ids`` 全部归属该项目（防 IDOR / 错跨项目）
    3. ``persistence.init_execution_record`` 写一行 status=pending
    4. ``EXECUTION_STREAM_HUB.register(eid)`` 必须在 ``asyncio.create_task``
       **之前**——否则前端可能抢先发 GET /stream 拿到 None，错过实时事件
    5. ``asyncio.create_task`` 启动 Engine（独立 DB session）
    6. 返回 ``ExecutionListItem`` 给前端，前端拿 id 去 GET /stream
    """
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    environment_id = await _resolve_environment_id(db, project_id, data.environment_id)
    await _validate_testcase_ownership(db, project_id, data.testcase_ids)

    execution_id = uuid.uuid4()
    config_snapshot = _build_config_snapshot(data, testcase_ids=data.testcase_ids)

    # 1) 写 pending 行：用 persistence 自带的 init 函数（独立 session）以保持
    #    Engine 后台 task 看到一致的状态，无需在请求 session 上提交。
    await persistence.init_execution_record(
        execution_id=execution_id,
        project_id=project_id,
        environment_id=environment_id,
        triggered_by=user.id,
        chat_message_id=data.chat_message_id,
        mode=data.mode,
        total_cases=len(data.testcase_ids),
        config_snapshot=config_snapshot,
    )

    # 2) 必须先 register 再起 task：前端可能在 5ms 内就发 SSE 订阅。
    await EXECUTION_STREAM_HUB.register(execution_id)

    # 3) 派后台任务。Engine 自管 DB session，不依赖此请求。
    inputs = ExecutionInputs(
        execution_id=execution_id,
        project_id=project_id,
        environment_id=environment_id,
        testcase_ids=list(data.testcase_ids),
        llm_config_id=data.llm_config_id,
        triggered_by=user.id,
        manual_overrides=dict(data.manual_overrides or {}),
        loaded_set_ids=list(data.loaded_set_ids or []),
        mode=data.mode,
        chat_message_id=data.chat_message_id,
        token_budget_override=data.token_budget,
        strict_data_mode=bool(data.strict_data_mode),
        module_entry_overrides=dict(data.module_entry_overrides or {}),
    )
    asyncio.create_task(_run_engine_background(inputs))

    # 4) 用刚写的行回包成 list item 返回（read-after-write，确保字段一致）
    row = await db.get(UIExecution, execution_id)
    if row is None:
        # 极少数情况：Engine 已经 flush 完且行被 cascade 删？理论不可能。
        raise AppException("执行已创建但读取失败，请刷新列表查看", code="EXEC_CREATED_READ_FAILED")
    return _to_list_item(row)


async def _run_engine_background(inputs: ExecutionInputs) -> None:
    """ExecutionEngine 入口的"裸调"包装：吞所有异常并落日志。

    Engine 内部已经有 try/finally 保证 ``flush_execution`` 与 ``mark_done``
    一定执行；这里只是再加一层保险，避免 background task 静默崩溃后没人发现。
    """
    try:
        engine = ExecutionEngine()
        await engine.run(inputs)
    except Exception:  # noqa: BLE001
        logger.exception(
            "ExecutionEngine background task crashed: execution_id=%s",
            inputs.execution_id,
        )


def _build_config_snapshot(
    data: ExecutionCreateRequest, *, testcase_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """复刻 Engine 的 ``_build_config_snapshot``。

    我们在 ``init_execution_record`` 里也存一份是为了：``retry-failed`` 复用、
    详情页"按本次配置重跑"按钮、审计追溯。**Engine 会在 flush 阶段再写一遍**，
    两份字段保持一致以避免歧义。
    """
    return {
        "testcase_ids": [str(x) for x in testcase_ids],
        "loaded_set_ids": [str(x) for x in (data.loaded_set_ids or [])],
        "manual_overrides": dict(data.manual_overrides or {}),
        "llm_config_id": str(data.llm_config_id) if data.llm_config_id else None,
        "token_budget_override": data.token_budget,
        "strict_data_mode": bool(data.strict_data_mode),
        "mode": data.mode,
        # 模块入口覆盖按 module_id 字符串化保存，retry-failed 重跑能恢复
        "module_entry_overrides": {
            str(k): v for k, v in (data.module_entry_overrides or {}).items()
        },
    }


# ─── Preflight：用例 → 涉及模块 + entry_path ─────────────────────────


async def preflight_modules(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: PreflightModulesRequest,
    user: User,
) -> PreflightModulesResponse:
    """给 ExecuteDialog 提交前看的"涉及模块清单"。

    返回 distinct 模块 + 当前 entry_path + 涉及的用例数。前端据此：
    - 渲染"测试地址"折叠区，按模块一行
    - 用 ``case_count`` 提示"该模块下有 X 条用例"
    - 用户在 UI 里临时改 entry_path 时，把覆盖值塞到
      ``ExecutionCreateRequest.module_entry_overrides[module_id]``

    没归模块的用例（``module_id IS NULL``）汇总到一行 ``module_id=None``，
    告诉用户"这些用例不走 entry_path 流程"。
    """
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    if not data.testcase_ids:
        return PreflightModulesResponse(items=[])

    # 用例 → module_id 映射；同时校验项目归属（防止跨项目串）
    from app.modules.testcases.models import Testcase, TestcaseModule

    rows = (
        await db.execute(
            select(Testcase.id, Testcase.module_id)
            .where(
                Testcase.id.in_(data.testcase_ids),
                Testcase.project_id == project_id,
            )
        )
    ).all()
    if not rows:
        return PreflightModulesResponse(items=[])

    # 统计每个 module 下的用例数
    counts: dict[uuid.UUID | None, int] = {}
    for _tid, mid in rows:
        counts[mid] = counts.get(mid, 0) + 1

    module_ids = [mid for mid in counts.keys() if mid is not None]
    module_meta: dict[uuid.UUID, tuple[str, str | None]] = {}
    if module_ids:
        meta_rows = (
            await db.execute(
                select(TestcaseModule.id, TestcaseModule.name, TestcaseModule.entry_path)
                .where(TestcaseModule.id.in_(module_ids))
            )
        ).all()
        module_meta = {row[0]: (row[1], row[2]) for row in meta_rows}

    items: list[PreflightModuleItem] = []
    # 有归属的模块：按 module 名排序，UI 看着顺
    for mid in sorted(module_ids, key=lambda x: module_meta.get(x, ("", None))[0]):
        name, entry_path = module_meta.get(mid, (None, None))
        items.append(
            PreflightModuleItem(
                module_id=mid,
                module_name=name,
                entry_path=entry_path,
                case_count=counts[mid],
            )
        )
    # 没归模块的用例兜底行（如果有）
    if None in counts:
        items.append(
            PreflightModuleItem(
                module_id=None,
                module_name=None,
                entry_path=None,
                case_count=counts[None],
            )
        )
    return PreflightModulesResponse(items=items)


# ─── 查询 ────────────────────────────────────────────────────────────


async def get_recent_config(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    testcase_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any] | None:
    """Task 10.1 — 返回"上一次执行该用例组合"的 config_snapshot。

    匹配策略：
    1. 给定 ``testcase_ids`` 时：在 ``ui_executions`` 里按 ``created_at desc``
       倒序找第一条 ``config_snapshot.testcase_ids`` **集合相等**（顺序无关）
       的执行；找不到则降级返回该项目最近一次执行的配置（任意用例组合）
    2. 没给 ``testcase_ids``：直接返回最近一次执行的配置

    返回 ``None`` 表示从未跑过——前端"复用上次"按钮会置灰。

    为什么不走精确 SQL JSONB 集合相等？JSONB 支持 ``@>`` / ``<@`` 但 sqlalchemy
    的可移植性差，且方向不对（数组顺序不同 ``@>`` 也成立）。简单做法：
    取最近 ``MAX_LOOKBACK`` 条，Python 侧做 set 相等。性能足够（用户不会
    在毫秒间触发多次执行）。
    """
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    # 取最近 N 条候选；之后用 Python 侧做 set 相等。性能足够（用户不会在
    # 毫秒间高频触发执行），且避免 JSONB ``@>`` 跨 DB 方言的兼容性问题。
    max_lookback = 50

    stmt = (
        select(UIExecution.config_snapshot, UIExecution.environment_id)
        .where(UIExecution.project_id == project_id)
        .where(UIExecution.config_snapshot.isnot(None))
        .order_by(desc(UIExecution.created_at))
        .limit(max_lookback)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return None

    target_ids: set[str] | None = None
    if testcase_ids:
        target_ids = {str(t) for t in testcase_ids}

    fallback_config: dict[str, Any] | None = None

    for snapshot, environment_id in rows:
        cfg = dict(snapshot or {})
        # 把 environment_id 一并塞进去，弹窗"复用"时一次到位（snapshot 里
        # 我们没存 env_id；它在表的独立列上）
        if environment_id is not None:
            cfg.setdefault("environment_id", str(environment_id))

        if target_ids is None:
            # 不需要匹配 — 第一条（最近一次）即返回
            return cfg

        snap_ids = cfg.get("testcase_ids") or []
        if isinstance(snap_ids, list) and {str(x) for x in snap_ids} == target_ids:
            return cfg
        # 降级候选：最近一条任意配置
        if fallback_config is None:
            fallback_config = cfg

    # 没找到完全匹配 → 退一步给"该项目最近一次配置"，这样新用例组合也
    # 能继承用户偏好的 LLM / token 预算 / 模式
    return fallback_config


async def list_executions(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
) -> tuple[list[ExecutionListItem], int]:
    """项目维度的执行列表。按 ``created_at desc`` 排序；可按 status 过滤。"""
    await _ensure_project_exists(db, project_id)
    await _check_project_member(db, project_id, user)

    query = (
        select(UIExecution)
        .where(UIExecution.project_id == project_id)
        .order_by(desc(UIExecution.created_at))
    )
    count_stmt = (
        select(func.count())
        .select_from(UIExecution)
        .where(UIExecution.project_id == project_id)
    )
    if status:
        query = query.where(UIExecution.status == status)
        count_stmt = count_stmt.where(UIExecution.status == status)

    total = (await db.execute(count_stmt)).scalar() or 0
    page_query = query.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(page_query)).scalars().unique().all()
    confidence_counts = await _confidence_counts_for_executions(
        db, [r.id for r in rows],
    )
    return [
        _to_list_item(r, confidence_counts.get(r.id, {}))
        for r in rows
    ], total


async def _confidence_counts_for_executions(
    db: AsyncSession,
    execution_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    """单次 GROUP BY 拿到本页所有 execution 的 ``data_confidence`` 三态计数。

    用于列表页"业务/执行"双视图通过率切换：
    - 业务视图：``passed_cases / (total_cases - data_failure_cases)``
    - 执行视图：``passed_cases / total_cases``

    若传入空列表直接返回 ``{}``，不做无谓的 SQL 调用。
    """
    if not execution_ids:
        return {}
    stmt = (
        select(
            UICaseResult.execution_id,
            UICaseResult.data_confidence,
            func.count(),
        )
        .where(UICaseResult.execution_id.in_(execution_ids))
        .group_by(UICaseResult.execution_id, UICaseResult.data_confidence)
    )
    out: dict[uuid.UUID, dict[str, int]] = {}
    for eid, conf, cnt in (await db.execute(stmt)).all():
        out.setdefault(eid, {})[conf] = int(cnt)
    return out


async def get_execution_detail(
    db: AsyncSession, execution_id: uuid.UUID, user: User,
) -> ExecutionDetailResponse:
    """完整详情：execution + cases + steps 一次取齐（嵌套 selectinload）。"""
    stmt = (
        select(UIExecution)
        .options(
            selectinload(UIExecution.case_results).selectinload(
                UICaseResult.step_results,
            ),
        )
        .where(UIExecution.id == execution_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundException("执行记录不存在")
    await _check_project_member(db, row.project_id, user)
    effective_budget = await _compute_effective_token_budget(db, row)
    testcase_meta = await _load_testcase_meta_for_cases(db, row.case_results)
    return _to_detail(
        row,
        effective_token_budget=effective_budget,
        testcase_meta=testcase_meta,
    )


async def _load_testcase_meta_for_cases(
    db: AsyncSession, case_results: list[UICaseResult],
) -> dict[uuid.UUID, dict[str, Any]]:
    """一次性把本次执行涉及的所有用例 + 模块查出来，给序列化用做 lookup。

    返回形状：``{testcase_id: {"title": str, "module_id": uuid, "module_name": str|None}}``。
    用例已删除时不会出现在 dict 里；调用方按缺省 ``None`` 处理。
    """
    from app.modules.testcases.models import Testcase, TestcaseModule

    case_ids = [
        c.testcase_id for c in case_results if c.testcase_id is not None
    ]
    if not case_ids:
        return {}
    stmt = (
        select(
            Testcase.id,
            Testcase.case_no,
            Testcase.title,
            Testcase.module_id,
            TestcaseModule.name,
        )
        .outerjoin(TestcaseModule, Testcase.module_id == TestcaseModule.id)
        .where(Testcase.id.in_(case_ids))
    )
    rows = (await db.execute(stmt)).all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for tid, case_no, title, module_id, module_name in rows:
        out[tid] = {
            "case_no": case_no,
            "title": title,
            "module_id": module_id,
            "module_name": module_name,
        }
    return out


async def _compute_effective_token_budget(
    db: AsyncSession, row: UIExecution,
) -> int:
    """计算执行生效预算：override > environment.token_budget > 兜底 50k。

    前端进度条最大值来自这里；不再写死，以免"环境改了 token 但监控页还
    显示老值"的困惑。
    """
    override = (row.config_snapshot or {}).get("token_budget_override")
    if isinstance(override, int) and override > 0:
        return override
    if row.environment_id is not None:
        env = (
            await db.execute(
                select(TestEnvironment.token_budget)
                .where(TestEnvironment.id == row.environment_id)
            )
        ).scalar_one_or_none()
        if isinstance(env, int) and env > 0:
            return env
    return 50_000


async def get_execution_or_404(
    db: AsyncSession, execution_id: uuid.UUID, user: User,
) -> UIExecution:
    """轻量查询版本：用于 stop / video / trace / SSE 这些不需要嵌套数据的端点。"""
    row = await db.get(UIExecution, execution_id)
    if row is None:
        raise NotFoundException("执行记录不存在")
    await _check_project_member(db, row.project_id, user)
    return row


async def get_step_or_404(
    db: AsyncSession, step_id: uuid.UUID, user: User,
) -> UIStepResult:
    """Task 10.5：截图端点用——按 step_id 取 step + 项目成员校验。

    通过 ``case_result_id`` 反查 case，再反查 execution 取 ``project_id``，
    最后做 ``_check_project_member``。同时把整条 chain 都加载，调用方拿到
    后能直接读 ``step.case_result.execution.project_id`` 等字段（虽然当前
    端点只用 ``screenshot_path``）。
    """
    stmt = (
        select(UIStepResult)
        .options(
            selectinload(UIStepResult.case_result).selectinload(
                UICaseResult.execution,
            ),
        )
        .where(UIStepResult.id == step_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundException("步骤记录不存在")
    project_id = row.case_result.execution.project_id
    await _check_project_member(db, project_id, user)
    return row


# ─── 停止 / 重试 ─────────────────────────────────────────────────────


async def continue_debug_execution(
    db: AsyncSession, execution_id: uuid.UUID, user: User,
) -> ExecutionContinueResponse:
    """Task 9.7 — 给 debug 模式中卡在 step_paused 的 execution 发 continue 信号。

    幂等：execution 不在 debug 暂停 / 已结束 → ``signal_delivered=False``，
    不报错。这样前端可以"狂点继续按钮"也不会拿到 4xx。
    """
    row = await get_execution_or_404(db, execution_id, user)
    delivered = await DEBUG_CONTROL_HUB.signal_continue(execution_id)
    return ExecutionContinueResponse(
        execution_id=execution_id,
        signal_delivered=delivered,
        status=row.status,
    )


async def delete_execution(
    db: AsyncSession, execution_id: uuid.UUID, user: User,
) -> dict[str, Any]:
    """硬删除一次执行 + 清理关联 artifact 文件。

    业务规则：
    * **必须是终态**——running / pending 状态禁止删除，需要先 stop 再删，
      避免 Engine 还在写文件时 race。前端按钮在非终态下 disable 即可，但
      service 层再校验一次以防 API 直接调用。
    * **级联**：``ui_case_results`` / ``ui_step_results`` 通过 FK
      ``ON DELETE CASCADE`` 自动删除，**但磁盘文件不会**——这里手工
      ``safe_unlink`` video / trace / 每个 step 的 screenshot，避免成"孤
      文件"占磁盘（``cleanup_old_media`` 是按 ``completed_at < cutoff``
      扫的，不是按 execution_id，所以删 DB 后磁盘文件再也扫不到）。
    * **去掉 hub 订阅**：active hub 里如果还有订阅者（不应该存在，因为
      非终态不让删；但兜底处理），把订阅者 evict 掉避免悬挂。

    返回 ``{"execution_id": str, "deleted": True}`` 给前端确认。
    """
    from app.modules.ui_automation.cleanup import safe_unlink
    from app.modules.ui_automation.models import UICaseResult, UIStepResult

    row = await get_execution_or_404(db, execution_id, user)
    if row.status not in TERMINAL_STATUSES:
        raise AppException(
            message=(
                f"不能删除非终态执行（当前 status={row.status}）。"
                "请先点击「停止」按钮终止执行后再删除。"
            ),
            code="EXECUTION_NOT_TERMINAL",
            status_code=409,
        )

    # ─ 1. 收集关联文件路径（在删 DB 前一次性 SELECT 出来）─
    file_paths: list[str] = []
    if row.video_path:
        file_paths.append(row.video_path)
    if row.trace_path:
        file_paths.append(row.trace_path)
    step_q = (
        select(UIStepResult.screenshot_path)
        .join(UICaseResult, UIStepResult.case_result_id == UICaseResult.id)
        .where(
            UICaseResult.execution_id == execution_id,
            UIStepResult.screenshot_path.is_not(None),
        )
    )
    file_paths.extend(
        p for p in (await db.execute(step_q)).scalars().all() if p
    )

    # ─ 2. 删 DB 行（cases/steps 走 ON DELETE CASCADE） ─
    await db.delete(row)
    await db.flush()

    # ─ 3. 删磁盘文件（DB commit 在外层 dependency 处理）─
    # 即便文件删失败也不影响 DB 已删的事实——下一次 cleanup_orphan 兜底
    deleted_files = 0
    errors: list[str] = []
    for p in file_paths:
        if safe_unlink(p, on_error=errors):
            deleted_files += 1

    # ─ 4. evict hub 订阅者（极端情况兜底）─
    # 终态执行通常 hub 已经在 30 分钟内 evict 掉，但万一还在内存里也手动
    # 释放避免悬挂订阅。失败不影响主流程（删除已完成）。
    try:
        await EXECUTION_STREAM_HUB.unregister(execution_id)
    except Exception:
        pass

    return {
        "execution_id": str(execution_id),
        "deleted": True,
        "files_deleted": deleted_files,
        "file_errors": errors,
    }


async def stop_execution(
    db: AsyncSession, execution_id: uuid.UUID, user: User,
) -> ExecutionStopResponse:
    """把 status 改成 ``stopped``。

    幂等：终态执行不报错，``already_terminal=True`` 让前端展示"该执行已结束"。
    Engine 在每个 case 之间会调 ``is_execution_stopped`` 自然中止。
    """
    row = await get_execution_or_404(db, execution_id, user)
    if row.status in TERMINAL_STATUSES:
        return ExecutionStopResponse(
            execution_id=execution_id,
            status=row.status,
            already_terminal=True,
        )
    row.status = "stopped"
    await db.flush()
    return ExecutionStopResponse(
        execution_id=execution_id,
        status="stopped",
        already_terminal=False,
    )


async def retry_failed_execution(
    db: AsyncSession,
    execution_id: uuid.UUID,
    data: ExecutionRetryRequest,
    user: User,
) -> ExecutionListItem:
    """从原 execution 抽出失败/错误/跳过的用例 + 复用配置，跑一次新的 execution。

    特殊情况：原 execution 全 passed → 报 400 给前端"无失败用例可重跑"；
    原 execution 用例已被删 → 自动跳过被删的，剩下的能跑就跑；都被删则报 404。
    """
    stmt = (
        select(UIExecution)
        .options(selectinload(UIExecution.case_results))
        .where(UIExecution.id == execution_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundException("执行记录不存在")
    await _check_project_member(db, row.project_id, user)

    failed_testcase_ids: list[uuid.UUID] = []
    for case in row.case_results:
        if case.status not in ("failed", "error", "skipped"):
            continue
        if case.testcase_id is None:  # 用例已被删，跳过
            continue
        failed_testcase_ids.append(case.testcase_id)

    if not failed_testcase_ids:
        raise AppException(
            "本次执行没有可重跑的用例（全部 passed 或对应 testcase 已被删除）",
            code="NO_FAILED_CASES",
            status_code=400,
        )

    config = dict(row.config_snapshot or {})
    merged_overrides = dict(config.get("manual_overrides") or {})
    merged_overrides.update(data.extra_manual_overrides or {})
    merged_loaded_sets = [
        uuid.UUID(x) for x in (config.get("loaded_set_ids") or [])
    ]
    merged_loaded_sets.extend(data.extra_loaded_set_ids or [])

    # module_entry_overrides 从 snapshot 还原（key 是字符串，需要转回 UUID）
    snapshot_module_overrides: dict[uuid.UUID, str] = {}
    for raw_k, raw_v in (config.get("module_entry_overrides") or {}).items():
        try:
            snapshot_module_overrides[uuid.UUID(str(raw_k))] = str(raw_v)
        except (ValueError, TypeError):
            # 旧执行记录没这个字段或者 key 已不是合法 UUID 时静默跳过
            continue

    new_request = ExecutionCreateRequest(
        testcase_ids=failed_testcase_ids,
        environment_id=data.environment_id or row.environment_id,
        mode=row.mode if row.mode in ("normal", "debug") else "normal",
        llm_config_id=data.llm_config_id or _maybe_uuid(config.get("llm_config_id")),
        loaded_set_ids=merged_loaded_sets,
        manual_overrides=merged_overrides,
        token_budget=(
            data.token_budget
            if data.token_budget is not None
            else config.get("token_budget_override")
        ),
        strict_data_mode=(
            data.strict_data_mode
            if data.strict_data_mode is not None
            else bool(config.get("strict_data_mode"))
        ),
        chat_message_id=None,  # 重跑不挂在原 chat message 上
        module_entry_overrides=snapshot_module_overrides,
    )
    return await start_execution(db, row.project_id, new_request, user)


# ─── 内部 helpers ────────────────────────────────────────────────────


async def _resolve_environment_id(
    db: AsyncSession, project_id: uuid.UUID, env_id: uuid.UUID | None,
) -> uuid.UUID:
    """决定本次执行用哪个 environment：

    - 用户传了 ``env_id`` → 验证它确实属于该项目（防跨项目使用别人的环境）
    - 没传 → 取项目下最新创建的环境
    - 项目下没环境 → 报 400 强制用户去配置（不像 Engine 那样兜底用 stub，因
      为这是 HTTP 入口，应该让用户**显式**地修配置）
    """
    if env_id is not None:
        env = await db.get(TestEnvironment, env_id)
        if env is None:
            raise NotFoundException("测试环境不存在")
        if env.project_id != project_id:
            # 用 NotFound 不暴露"环境存在但属于别的项目"
            raise NotFoundException("测试环境不存在")
        return env.id

    stmt = (
        select(TestEnvironment.id)
        .where(TestEnvironment.project_id == project_id)
        .order_by(desc(TestEnvironment.created_at))
        .limit(1)
    )
    fallback = (await db.execute(stmt)).scalar_one_or_none()
    if fallback is None:
        raise AppException(
            "项目下还没有测试环境，请先到 UI 自动化 → 环境配置 创建",
            code="NO_ENVIRONMENT",
            status_code=400,
        )
    return fallback


async def _validate_testcase_ownership(
    db: AsyncSession, project_id: uuid.UUID, testcase_ids: list[uuid.UUID],
) -> None:
    """所有 testcase_ids 必须归属同一个项目。

    这是 IDOR 防御的关键：前端只发了 id，必须验证 id 不是别的项目的，否则
    A 项目的人可以"借"B 项目的用例触发执行。
    """
    if not testcase_ids:
        raise AppException("testcase_ids 不能为空", code="EMPTY_TESTCASES", status_code=400)
    from app.modules.testcases.models import Testcase

    stmt = select(Testcase.id).where(
        Testcase.id.in_(testcase_ids),
        Testcase.project_id == project_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    found = set(rows)
    missing = [str(x) for x in testcase_ids if x not in found]
    if missing:
        raise AppException(
            f"以下用例不存在或不属于当前项目：{', '.join(missing[:5])}"
            + ("..." if len(missing) > 5 else ""),
            code="TESTCASE_NOT_IN_PROJECT",
            status_code=400,
        )


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _to_list_item(
    row: UIExecution,
    confidence_counts: dict[str, int] | None = None,
) -> ExecutionListItem:
    """``confidence_counts`` 来自一次性 GROUP BY；省略时三个字段都是 0。

    单条执行（``start_execution`` 等场景）调用本函数不需要 confidence——
    那时执行还没产生用例结果，全 0 即正确。
    """
    counts = confidence_counts or {}
    return ExecutionListItem(
        id=row.id,
        project_id=row.project_id,
        environment_id=row.environment_id,
        status=row.status,
        mode=row.mode,
        total_cases=row.total_cases,
        passed_cases=row.passed_cases,
        failed_cases=row.failed_cases,
        skipped_cases=row.skipped_cases,
        reliable_cases=counts.get("reliable", 0),
        synthesized_cases=counts.get("synthesized", 0),
        data_failure_cases=counts.get("data_failure", 0),
        duration_ms=row.duration_ms,
        tokens_total=row.tokens_total or 0,
        has_video=bool(row.video_path),
        has_trace=bool(row.trace_path),
        triggered_by=row.triggered_by,
        chat_message_id=row.chat_message_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_detail(
    row: UIExecution,
    *,
    effective_token_budget: int = 50_000,
    testcase_meta: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> ExecutionDetailResponse:
    meta = testcase_meta or {}
    return ExecutionDetailResponse(
        id=row.id,
        project_id=row.project_id,
        environment_id=row.environment_id,
        status=row.status,
        mode=row.mode,
        total_cases=row.total_cases,
        passed_cases=row.passed_cases,
        failed_cases=row.failed_cases,
        skipped_cases=row.skipped_cases,
        duration_ms=row.duration_ms,
        tokens_total=row.tokens_total or 0,
        video_path=row.video_path,
        trace_path=row.trace_path,
        # video_url / trace_url 是 nginx 静态路径（``/uploads/ui_artifacts/...``），
        # 让前端 ``<video src>`` / ``<a href download>`` 直接用——HTML media 标签
        # 不会自动带 Authorization header，必须走静态路径绕开后端鉴权。
        video_url=_artifact_path_to_url(row.video_path),
        trace_url=_artifact_path_to_url(row.trace_path),
        has_video=bool(row.video_path),
        has_trace=bool(row.trace_path),
        chat_message_id=row.chat_message_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        triggered_by=row.triggered_by,
        test_data_snapshot=row.test_data_snapshot,
        config_snapshot=dict(row.config_snapshot or {}),
        error_message=row.error_message,
        effective_token_budget=effective_token_budget,
        created_at=row.created_at,
        updated_at=row.updated_at,
        case_results=[_to_case_response(c, meta) for c in row.case_results],
    )


def _to_case_response(
    case: UICaseResult,
    testcase_meta: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> ExecutionCaseResponse:
    meta = (
        (testcase_meta or {}).get(case.testcase_id)
        if case.testcase_id is not None
        else None
    )
    return ExecutionCaseResponse(
        id=case.id,
        execution_id=case.execution_id,
        testcase_id=case.testcase_id,
        testcase_no=(meta or {}).get("case_no"),
        testcase_title=(meta or {}).get("title"),
        testcase_module_id=(meta or {}).get("module_id"),
        testcase_module_name=(meta or {}).get("module_name"),
        status=case.status,
        error_message=case.error_message,
        ai_summary=case.ai_summary,
        duration_ms=case.duration_ms,
        tokens_used=case.tokens_used or 0,
        sort_order=case.sort_order,
        test_data_used=case.test_data_used,
        synthesized_data=list(case.synthesized_data or []),
        data_failures=list(case.data_failures or []),
        data_confidence=case.data_confidence,
        started_at=case.started_at,
        completed_at=case.completed_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        steps=[_to_step_response(s) for s in case.step_results],
    )


def _artifact_path_to_url(path: str | None) -> str | None:
    """把 ``ui_artifacts`` 目录下的后端绝对路径转换成 nginx 可直接出文件的 web URL。

    设计依据：``frontend/nginx.conf`` 把 ``/uploads/ui_artifacts/`` location
    alias 到 ``/app/uploads/ui_artifacts/``（**无需** Bearer token），所以前端
    ``<img src>`` / ``<video src>`` / ``<a href>`` 可以直接用这条路径。

    **关键**：``<video>`` / ``<audio>`` / ``<img>`` 标签发请求时浏览器**不会**
    自动带 Authorization header（axios interceptor 不参与），所以**必须**用
    nginx 静态路径而非鉴权 API 路径——否则前端会看到 401，video 标签触发
    ``onerror`` 回调显示"视频加载失败"（实际故障：截图正常 / 视频显示失败）。

    后端不 hard-code "/app/uploads"，而是从 ``settings.UI_ARTIFACTS_DIR`` 取
    根目录做相对路径计算，避免开发 / 生产环境路径不同的耦合。
    """
    if not path:
        return None
    from app.config import settings

    root = os.path.abspath(settings.UI_ARTIFACTS_DIR)
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(root + os.sep) and abs_path != root:
        # 不是 ui_artifacts 子路径（典型不会发生，兜底返回 None 让前端展示
        # "暂无内容"，而不是给一个非法 URL）
        return None
    rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
    return f"/uploads/ui_artifacts/{rel}"


# 为兼容历史调用（任何地方还在 import 这个旧名字）保留 alias。新代码请用
# ``_artifact_path_to_url``——名字反映了它对所有 artifact 类型（截图 / 视频 /
# trace）通用，而不是误以为只服务于截图。
_screenshot_path_to_url = _artifact_path_to_url


def _to_step_response(step: UIStepResult) -> ExecutionStepResponse:
    return ExecutionStepResponse(
        id=step.id,
        case_result_id=step.case_result_id,
        step_number=step.step_number,
        description=step.description,
        expected_result=step.expected_result,
        tool_calls=list(step.tool_calls or []),
        ai_reasoning=step.ai_reasoning,
        snapshot_before=step.snapshot_before,
        snapshot_after=step.snapshot_after,
        assertion_passed=step.assertion_passed,
        assertion_reason=step.assertion_reason,
        assertion_evidence=step.assertion_evidence,
        status=step.status,
        screenshot_path=step.screenshot_path,
        screenshot_url=_screenshot_path_to_url(step.screenshot_path),
        error_message=step.error_message,
        retry_count=step.retry_count or 0,
        tokens_used=step.tokens_used or 0,
        duration_ms=step.duration_ms,
        created_at=step.created_at,
        updated_at=step.updated_at,
    )


# ─── SSE 订阅 ────────────────────────────────────────────────────────


async def subscribe_execution_stream(
    execution_id: uuid.UUID,
    user: User,
):
    """SSE 订阅入口：迭代 execution stream 的事件并 yield ``data: <json>`` 帧。

    与一期 ``subscribe_chat_stream`` 的策略对齐：
    - 内存 hub 里有 stream → 实时回放（包括已缓存的历史事件 + 新事件）
    - hub 里没有（任务已完成且 evict / 服务重启） → 从 DB 拼一个 done 事件返回，
      让前端自然关闭 EventSource，剩下的详细数据通过 ``GET /ui-executions/{id}``
      取
    - 全程不断 db session（用 ``async_session_factory()`` 自管）
    """
    from app.database import async_session_factory
    from app.modules.ui_automation.sse import sse_done, sse_encode, sse_error

    async with async_session_factory() as db:
        row = await db.get(UIExecution, execution_id)
        if row is None:
            yield sse_error("执行记录不存在")
            yield sse_done()
            return
        try:
            await _check_project_member(db, row.project_id, user)
        except (NotFoundException, AppException):
            yield sse_error("无权访问该执行记录")
            yield sse_done()
            return
        terminal = row.status in TERMINAL_STATUSES

    stream = EXECUTION_STREAM_HUB.get(execution_id)
    if stream is None:
        # 任务已完成 + stream 已被 evict / 服务重启：直接 done 让前端去拉详情。
        if terminal:
            yield sse_encode({
                "type": "execution_complete",
                "execution_id": str(execution_id),
                "replay_only": True,
            })
        else:
            yield sse_encode({
                "type": "info",
                "message": "执行流已不在内存中，请刷新查看最终结果",
            })
        yield sse_done({"execution_id": str(execution_id)})
        return

    async for event_name, event_data in stream.subscribe():
        # event_data 在 Engine 里已经是 dict；这里给它补上 type 字段（如果没的
        # 话）以保证 SSE 协议字段稳定。
        if not isinstance(event_data, dict):
            continue
        if "type" not in event_data:
            event_data = {"type": event_name, **event_data}
        yield sse_encode(event_data)

    yield sse_done({"execution_id": str(execution_id)})


__all__ = [
    "TERMINAL_STATUSES",
    "continue_debug_execution",
    "delete_execution",
    "get_execution_detail",
    "get_execution_or_404",
    "get_recent_config",
    "list_executions",
    "preflight_modules",
    "retry_failed_execution",
    "start_execution",
    "stop_execution",
    "subscribe_execution_stream",
]
