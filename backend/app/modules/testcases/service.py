"""测试用例模块树与用例 CRUD 服务。"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException, NotFoundException
from app.modules.auth.models import User
from app.modules.testcases.models import Testcase, TestcaseModule, TestcaseStep
from app.modules.testcases.schemas import (
    ModuleCreateRequest,
    ModuleResponse,
    ModuleTreeNode,
    ModuleUpdateRequest,
    StepResponse,
    TestcaseCreateRequest,
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
