"""测试用例模块树与用例 CRUD 服务。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException
from app.modules.auth.models import User
from app.modules.testcases.excel_io import (
    EXCEL_IMPORT_MAX_ROWS,
    ParsedRow,
    build_export_workbook,
    build_module_path_index,
    build_template_workbook,
    join_module_path,
    parse_workbook,
    split_module_path,
)
from app.modules.testcases.models import Testcase, TestcaseModule, TestcaseStep
from app.modules.testcases.schemas import (
    ModuleCreateRequest,
    ModuleResponse,
    ModuleTreeNode,
    ModuleUpdateRequest,
    StepResponse,
    TestcaseCreateRequest,
    TestcaseImportError,
    TestcaseImportReport,
    TestcaseListItem,
    TestcaseResponse,
    TestcaseUpdateRequest,
)

# ════════════════════════════════════════
#  模块树
# ════════════════════════════════════════


async def create_module(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: ModuleCreateRequest,
) -> ModuleResponse:
    if data.parent_id:
        parent = await _get_module_or_404(db, data.parent_id)
        if parent.project_id != project_id:
            raise AppException("父模块不属于当前项目", code="INVALID_PARENT", status_code=400)

    module = TestcaseModule(
        project_id=project_id,
        parent_id=data.parent_id,
        name=data.name,
        order_index=data.order_index,
        entry_path=_normalize_entry_path(data.entry_path),
    )
    db.add(module)
    await db.flush()
    await db.refresh(module)
    return _to_module_response(module)


async def update_module(
    db: AsyncSession,
    module_id: uuid.UUID,
    data: ModuleUpdateRequest,
) -> ModuleResponse:
    module = await _get_module_or_404(db, module_id)

    if data.name is not None:
        module.name = data.name
    if data.order_index is not None:
        module.order_index = data.order_index
    if data.parent_id is not None:
        if data.parent_id == module.id:
            raise AppException("不能将模块设为自身的子模块", code="INVALID_PARENT", status_code=400)
        module.parent_id = data.parent_id
    # entry_path 用 fields_set 区分"未传 vs 传 None/空串"——前端"清空入口路径"
    # 应该把字段显式置 None；不动入口路径就别传这个字段。
    if "entry_path" in data.model_fields_set:
        module.entry_path = _normalize_entry_path(data.entry_path)

    await db.flush()
    await db.refresh(module)
    return _to_module_response(module)


async def delete_module(db: AsyncSession, module_id: uuid.UUID) -> None:
    module = await _get_module_or_404(db, module_id)
    await db.delete(module)


async def get_module_tree(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[ModuleTreeNode]:
    result = await db.execute(
        select(TestcaseModule)
        .where(TestcaseModule.project_id == project_id)
        .order_by(TestcaseModule.order_index)
    )
    all_modules = list(result.scalars().unique().all())

    count_rows = await db.execute(
        select(Testcase.module_id, func.count(Testcase.id))
        .where(Testcase.project_id == project_id)
        .group_by(Testcase.module_id)
    )
    direct_counts: dict[uuid.UUID | None, int] = {row[0]: row[1] for row in count_rows}

    modules_by_parent: dict[uuid.UUID | None, list[TestcaseModule]] = {}
    for m in all_modules:
        modules_by_parent.setdefault(m.parent_id, []).append(m)

    def build_tree(parent_id: uuid.UUID | None) -> list[ModuleTreeNode]:
        nodes: list[ModuleTreeNode] = []
        for m in modules_by_parent.get(parent_id, []):
            children = build_tree(m.id)
            count = direct_counts.get(m.id, 0) + sum(c.case_count for c in children)
            nodes.append(
                ModuleTreeNode(
                    id=m.id,
                    name=m.name,
                    parent_id=m.parent_id,
                    order_index=m.order_index,
                    entry_path=m.entry_path,
                    case_count=count,
                    children=children,
                )
            )
        return nodes

    return build_tree(None)


# ════════════════════════════════════════
#  测试用例 CRUD
# ════════════════════════════════════════


async def create_testcase(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: TestcaseCreateRequest,
    user: User,
) -> TestcaseResponse:
    if data.module_id:
        module = await _get_module_or_404(db, data.module_id)
        if module.project_id != project_id:
            raise AppException("模块不属于当前项目", code="INVALID_MODULE", status_code=400)

    case_no = await _allocate_case_no(db, project_id)

    testcase = Testcase(
        project_id=project_id,
        case_no=case_no,
        module_id=data.module_id,
        title=data.title,
        precondition=data.precondition,
        priority=data.priority,
        source="manual",
        created_by=user.id,
        default_data_set_ids=[str(sid) for sid in data.default_data_set_ids],
    )
    db.add(testcase)
    await db.flush()

    for step_data in data.steps:
        step = TestcaseStep(
            testcase_id=testcase.id,
            step_number=step_data.step_number,
            action=step_data.action,
            expected_result=step_data.expected_result,
        )
        db.add(step)

    await db.flush()
    await db.refresh(testcase)
    return _to_testcase_response(testcase)


async def update_testcase(
    db: AsyncSession,
    testcase_id: uuid.UUID,
    data: TestcaseUpdateRequest,
) -> TestcaseResponse:
    testcase = await _get_testcase_or_404(db, testcase_id)

    if data.title is not None:
        testcase.title = data.title
    if data.module_id is not None:
        testcase.module_id = data.module_id
    if data.precondition is not None:
        testcase.precondition = data.precondition
    if data.priority is not None:
        testcase.priority = data.priority
    if data.status is not None:
        testcase.status = data.status
    if data.exec_result is not None:
        testcase.exec_result = data.exec_result
    if data.default_data_set_ids is not None:
        # 传空数组就是"清空"；传 None 才是"不改"
        testcase.default_data_set_ids = [str(sid) for sid in data.default_data_set_ids]

    if data.steps is not None:
        for old_step in testcase.steps:
            await db.delete(old_step)
        await db.flush()

        for step_data in data.steps:
            step = TestcaseStep(
                testcase_id=testcase.id,
                step_number=step_data.step_number,
                action=step_data.action,
                expected_result=step_data.expected_result,
            )
            db.add(step)

    await db.flush()
    await db.refresh(testcase)
    return _to_testcase_response(testcase)


async def delete_testcase(db: AsyncSession, testcase_id: uuid.UUID) -> None:
    testcase = await _get_testcase_or_404(db, testcase_id)
    await db.delete(testcase)


async def get_testcase(db: AsyncSession, testcase_id: uuid.UUID) -> TestcaseResponse:
    testcase = await _get_testcase_or_404(db, testcase_id)
    return _to_testcase_response(testcase)


async def list_testcases(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    module_id: uuid.UUID | None = None,
    priority: str | None = None,
    status: str | None = None,
    source: str | None = None,
    exec_result: str | None = None,
    search: str | None = None,
) -> tuple[list[TestcaseListItem], int]:
    base_query = select(Testcase).where(Testcase.project_id == project_id)
    count_query = select(func.count()).select_from(Testcase).where(Testcase.project_id == project_id)

    if module_id:
        # 与模块树的总数 (case_count = 自身 + 全部后代) 保持一致：
        # 选中父模块时也展示其全部后代模块下的用例。
        module_ids = await _collect_module_ids_with_descendants(db, project_id, module_id)
        base_query = base_query.where(Testcase.module_id.in_(module_ids))
        count_query = count_query.where(Testcase.module_id.in_(module_ids))
    if priority:
        base_query = base_query.where(Testcase.priority == priority)
        count_query = count_query.where(Testcase.priority == priority)
    if status:
        base_query = base_query.where(Testcase.status == status)
        count_query = count_query.where(Testcase.status == status)
    if source:
        base_query = base_query.where(Testcase.source == source)
        count_query = count_query.where(Testcase.source == source)
    if exec_result:
        base_query = base_query.where(Testcase.exec_result == exec_result)
        count_query = count_query.where(Testcase.exec_result == exec_result)
    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(Testcase.title.ilike(pattern))
        count_query = count_query.where(Testcase.title.ilike(pattern))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        base_query
        .options(selectinload(Testcase.module), selectinload(Testcase.creator))
        .order_by(Testcase.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    testcases = list(result.scalars().unique().all())

    items = [
        TestcaseListItem(
            id=tc.id,
            case_no=tc.case_no,
            module_id=tc.module_id,
            module_name=tc.module.name if tc.module else None,
            title=tc.title,
            priority=tc.priority,
            status=tc.status,
            source=tc.source,
            exec_result=tc.exec_result,
            creator_name=(
                tc.creator.display_name or tc.creator.username if tc.creator else None
            ),
            created_at=tc.created_at,
            updated_at=tc.updated_at,
        )
        for tc in testcases
    ]
    return items, total


# ════════════════════════════════════════
#  Internal helpers
# ════════════════════════════════════════


def _to_module_response(module: TestcaseModule) -> ModuleResponse:
    return ModuleResponse(
        id=module.id,
        project_id=module.project_id,
        parent_id=module.parent_id,
        name=module.name,
        order_index=module.order_index,
        entry_path=module.entry_path,
        created_at=module.created_at,
        updated_at=module.updated_at,
    )


def _normalize_entry_path(value: str | None) -> str | None:
    """统一 entry_path 入库前的清洗。

    - 空串 / 纯空白 → ``None``（避免后续 ``base_url + ""`` 这种奇怪拼接）
    - 否则去掉首尾空白，原样保存
    - 不在这里强制要求 "/" 开头：允许写完整 URL（``https://...``）做跨子域跳转
    """
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _to_testcase_response(testcase: Testcase) -> TestcaseResponse:
    return TestcaseResponse(
        id=testcase.id,
        case_no=testcase.case_no,
        project_id=testcase.project_id,
        module_id=testcase.module_id,
        module_name=testcase.module.name if testcase.module else None,
        title=testcase.title,
        precondition=testcase.precondition,
        priority=testcase.priority,
        status=testcase.status,
        source=testcase.source,
        exec_result=testcase.exec_result,
        created_by=testcase.created_by,
        creator_name=(
            testcase.creator.display_name or testcase.creator.username
            if testcase.creator else None
        ),
        steps=[
            StepResponse(
                id=s.id,
                testcase_id=s.testcase_id,
                step_number=s.step_number,
                action=s.action,
                expected_result=s.expected_result,
                created_at=s.created_at,
            )
            for s in testcase.steps
        ],
        default_data_set_ids=[
            # JSONB 列里存的是 str，Pydantic 会在 response 层做 UUID 解析
            uuid.UUID(str(sid))
            for sid in (testcase.default_data_set_ids or [])
        ],
        created_at=testcase.created_at,
        updated_at=testcase.updated_at,
    )


async def _collect_module_ids_with_descendants(
    db: AsyncSession,
    project_id: uuid.UUID,
    root_module_id: uuid.UUID,
) -> list[uuid.UUID]:
    """返回 ``root_module_id`` 自身 + 全部后代模块 id 列表（同项目内）。

    用于按模块筛选用例时把子模块下的用例也一并展示，避免出现"模块树显示
    有用例但列表为空"的反直觉行为。
    """
    result = await db.execute(
        select(TestcaseModule.id, TestcaseModule.parent_id)
        .where(TestcaseModule.project_id == project_id)
    )
    rows = result.all()
    children_map: dict[uuid.UUID | None, list[uuid.UUID]] = {}
    for mid, pid in rows:
        children_map.setdefault(pid, []).append(mid)

    collected: list[uuid.UUID] = []
    stack: list[uuid.UUID] = [root_module_id]
    while stack:
        current = stack.pop()
        collected.append(current)
        stack.extend(children_map.get(current, []))
    return collected


async def _allocate_case_no(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    count: int = 1,
) -> int:
    """为项目内新用例分配下一个 case_no。

    采用 SELECT MAX + N 的简单策略，依赖 (project_id, case_no) 唯一索引兜底。
    并发场景下若两个事务同时插入会有一方因唯一索引冲突回滚——这是该平台预期
    可接受的失败模式（前端会提示"创建失败请重试"），而不是引入复杂的 sequence
    或 advisory lock。

    若 ``count > 1``，返回值是"第一个"可用的 case_no；调用方需自行 +1 累加。
    """
    result = await db.execute(
        select(func.coalesce(func.max(Testcase.case_no), 0)).where(
            Testcase.project_id == project_id
        )
    )
    current_max = result.scalar() or 0
    return current_max + 1


async def _get_module_or_404(db: AsyncSession, module_id: uuid.UUID) -> TestcaseModule:
    result = await db.execute(
        select(TestcaseModule).where(TestcaseModule.id == module_id)
    )
    module = result.scalar_one_or_none()
    if not module:
        raise NotFoundException("模块不存在")
    return module


async def _get_testcase_or_404(db: AsyncSession, testcase_id: uuid.UUID) -> Testcase:
    result = await db.execute(
        select(Testcase)
        .options(
            selectinload(Testcase.steps),
            selectinload(Testcase.module),
            selectinload(Testcase.creator),
        )
        .where(Testcase.id == testcase_id)
    )
    testcase = result.scalar_one_or_none()
    if not testcase:
        raise NotFoundException("用例不存在")
    return testcase


# ════════════════════════════════════════
#  Excel 模板 / 导入 / 导出
# ════════════════════════════════════════


def get_template_xlsx() -> bytes:
    """返回标准用例模板（含示例行 + 字段说明 sheet）。"""
    return build_template_workbook(sample=True)


async def export_testcases_xlsx(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    module_id: uuid.UUID | None = None,
    priority: str | None = None,
    status: str | None = None,
    source: str | None = None,
    exec_result: str | None = None,
    search: str | None = None,
) -> bytes:
    """按当前列表筛选条件导出 xlsx。

    与 ``list_testcases`` 复用筛选语义，让"页面上看到什么 → 导出就是什么"，
    避免用户在导出时再被一次"看到了 200 条但只导了 20 条"的体验断点。
    导出**不分页**：导出本身就是"拿走全部"。
    """
    # 复用 list_testcases 的过滤构造，但去掉 limit/offset
    base_query = select(Testcase).where(Testcase.project_id == project_id)
    if module_id:
        module_ids = await _collect_module_ids_with_descendants(db, project_id, module_id)
        base_query = base_query.where(Testcase.module_id.in_(module_ids))
    if priority:
        base_query = base_query.where(Testcase.priority == priority)
    if status:
        base_query = base_query.where(Testcase.status == status)
    if source:
        base_query = base_query.where(Testcase.source == source)
    if exec_result:
        base_query = base_query.where(Testcase.exec_result == exec_result)
    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(Testcase.title.ilike(pattern))

    base_query = base_query.options(
        selectinload(Testcase.steps),
        selectinload(Testcase.module),
    ).order_by(Testcase.case_no.asc(), Testcase.created_at.asc())

    result = await db.execute(base_query)
    testcases = list(result.scalars().unique().all())

    # 一次性拉模块树构造 ``id → path`` 映射，避免按行查 DB
    modules_result = await db.execute(
        select(TestcaseModule).where(TestcaseModule.project_id == project_id)
    )
    modules = list(modules_result.scalars().unique().all())
    id_to_path, _ = build_module_path_index(modules)

    def resolver(module_id_value):
        if module_id_value is None:
            return ""
        return id_to_path.get(str(module_id_value), "")

    return build_export_workbook(testcases, resolver)


async def import_testcases_xlsx(
    db: AsyncSession,
    project_id: uuid.UUID,
    raw_xlsx: bytes,
    user: User,
) -> TestcaseImportReport:
    """从 xlsx 字节流批量导入用例。

    匹配规则（与 PRD 一致）：
    - 行内"用例编号"为空 → 新增
    - 行内"用例编号"项目里查不到 → 也按新增处理（**不**复用用户指定的编号，
      仍然用 ``_allocate_case_no`` 分配，避免占用未来连续号段）
    - 行内"用例编号"项目里能查到 → 全字段覆盖更新（含步骤、模块归属、状态等）

    模块路径：``a/b/c`` 形式；按层匹配现有模块；缺哪一层就建哪一层；多个同
    路径冲突的（罕见）报错给用户处理。
    """
    try:
        parsed_rows, parse_errors = parse_workbook(raw_xlsx)
    except ValueError as exc:
        raise AppException(str(exc), code="EXCEL_PARSE_ERROR", status_code=422) from exc

    report = TestcaseImportReport(
        total=len(parsed_rows),
        errors=[
            TestcaseImportError(row=e.row, message=e.message, title=e.title)
            for e in parse_errors
        ],
    )

    if not parsed_rows:
        return report

    # 预取模块树和已有用例（按 case_no 索引），避免按行 N+1 查询
    modules_result = await db.execute(
        select(TestcaseModule).where(TestcaseModule.project_id == project_id)
    )
    modules = list(modules_result.scalars().unique().all())
    id_to_path, path_to_modules = build_module_path_index(modules)

    cases_result = await db.execute(
        select(Testcase)
        .options(selectinload(Testcase.steps), selectinload(Testcase.module))
        .where(Testcase.project_id == project_id)
    )
    existing_cases: dict[int, Testcase] = {
        tc.case_no: tc for tc in cases_result.scalars().unique().all() if tc.case_no
    }

    # 用例编号一次性预取最大值；新增时本批次内自增不再额外查 DB
    next_case_no = await _allocate_case_no(db, project_id)
    created_module_paths: set[str] = set()

    for row in parsed_rows:
        try:
            module_id, newly_created = await _resolve_or_create_module(
                db, project_id, row, path_to_modules, id_to_path,
                created_module_paths, report,
            )
        except AppException as exc:
            report.errors.append(TestcaseImportError(
                row=row.row_no, title=row.title, message=exc.message,
            ))
            continue

        # 维护本批次的 newly_created 模块也加进 path 索引，让后续行能复用
        if newly_created is not None:
            path_tuple = tuple(split_module_path(_module_path_for_module(newly_created, id_to_path)))
            path_to_modules.setdefault(path_tuple, []).append(newly_created)

        if row.case_no and row.case_no in existing_cases:
            tc = existing_cases[row.case_no]
            await _apply_row_to_testcase(db, tc, row, module_id)
            report.updated += 1
        else:
            tc = await _create_testcase_from_row(
                db, project_id, row, module_id, user, allocated_case_no=next_case_no,
            )
            existing_cases[next_case_no] = tc
            next_case_no += 1
            report.created += 1

    await db.flush()

    return report


async def _resolve_or_create_module(
    db: AsyncSession,
    project_id: uuid.UUID,
    row: ParsedRow,
    path_to_modules: dict[tuple[str, ...], list[TestcaseModule]],
    id_to_path: dict[str | None, str],
    created_module_paths: set[str],
    report: TestcaseImportReport,
) -> tuple[uuid.UUID | None, TestcaseModule | None]:
    """根据 ParsedRow.module_path 解析或创建模块；返回 (module_id, 新建模块)。

    多 match 时（同 path 下竟然挂了多个模块——historic data quirk）报错让用户
    去后台合并，不擅自挑一个。
    """
    names = split_module_path(row.module_path)
    if not names:
        return None, None

    # 完整路径就在树里：取唯一一个；多个就报错
    full_tuple = tuple(names)
    matches = path_to_modules.get(full_tuple, [])
    if len(matches) == 1:
        return matches[0].id, None
    if len(matches) > 1:
        raise AppException(
            f"模块路径『{join_module_path(names)}』在项目内有多个同名匹配，请先合并后再导入",
            code="MODULE_PATH_AMBIGUOUS",
            status_code=400,
        )

    # 按层向下走，缺哪一层补哪一层
    parent: TestcaseModule | None = None
    parent_id: uuid.UUID | None = None
    new_module: TestcaseModule | None = None
    for depth in range(len(names)):
        sub_tuple = tuple(names[: depth + 1])
        sub_matches = [
            m for m in path_to_modules.get(sub_tuple, [])
            if (m.parent_id == parent_id) or (parent_id is None and m.parent_id is None)
        ]
        if len(sub_matches) == 1:
            parent = sub_matches[0]
            parent_id = parent.id
            continue
        if len(sub_matches) > 1:
            raise AppException(
                f"模块路径『{join_module_path(names[: depth + 1])}』有多个同名兄弟节点，请先合并后再导入",
                code="MODULE_PATH_AMBIGUOUS",
                status_code=400,
            )

        # 缺这一层 → 创建
        new_module = TestcaseModule(
            project_id=project_id,
            parent_id=parent_id,
            name=names[depth],
            order_index=0,
        )
        db.add(new_module)
        await db.flush()
        await db.refresh(new_module)

        # 维护索引：让本次导入的后续行也能命中这个新建模块
        id_to_path[str(new_module.id)] = join_module_path(names[: depth + 1])
        path_to_modules.setdefault(sub_tuple, []).append(new_module)

        path_str = join_module_path(names[: depth + 1])
        if path_str not in created_module_paths:
            created_module_paths.add(path_str)
            report.created_modules.append(path_str)

        parent = new_module
        parent_id = new_module.id

    return (parent.id if parent else None), new_module


def _module_path_for_module(
    m: TestcaseModule,
    id_to_path: dict[str | None, str],
) -> str:
    return id_to_path.get(str(m.id), m.name)


async def _create_testcase_from_row(
    db: AsyncSession,
    project_id: uuid.UUID,
    row: ParsedRow,
    module_id: uuid.UUID | None,
    user: User,
    *,
    allocated_case_no: int,
) -> Testcase:
    tc = Testcase(
        project_id=project_id,
        case_no=allocated_case_no,
        module_id=module_id,
        title=row.title,
        precondition=row.precondition,
        priority=row.priority,
        status=row.status,
        exec_result=row.exec_result,
        source="manual",
        created_by=user.id,
        default_data_set_ids=[],
    )
    db.add(tc)
    await db.flush()

    for step in row.steps:
        db.add(TestcaseStep(
            testcase_id=tc.id,
            step_number=step.step_number,
            action=step.action,
            expected_result=step.expected_result,
        ))
    await db.flush()
    return tc


async def _apply_row_to_testcase(
    db: AsyncSession,
    tc: Testcase,
    row: ParsedRow,
    module_id: uuid.UUID | None,
) -> None:
    """覆盖式更新：把 row 里出现的字段全都写进 tc，包括步骤替换。"""
    tc.title = row.title
    tc.precondition = row.precondition
    tc.priority = row.priority
    tc.status = row.status
    tc.exec_result = row.exec_result
    # 模块路径为空 = 用户在 Excel 里手动清空了 → 把用例归回"未分类"。
    # 这是导出/导入 round-trip 的可预期行为，不要悄悄保留旧 module_id。
    tc.module_id = module_id

    for old in list(tc.steps or []):
        await db.delete(old)
    await db.flush()

    for step in row.steps:
        db.add(TestcaseStep(
            testcase_id=tc.id,
            step_number=step.step_number,
            action=step.action,
            expected_result=step.expected_result,
        ))
    await db.flush()


__all__ = [
    "EXCEL_IMPORT_MAX_ROWS",
    "create_module",
    "create_testcase",
    "delete_module",
    "delete_testcase",
    "export_testcases_xlsx",
    "get_module_tree",
    "get_template_xlsx",
    "get_testcase",
    "import_testcases_xlsx",
    "list_testcases",
    "update_module",
    "update_testcase",
]
