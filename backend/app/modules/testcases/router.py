import io
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.exceptions import AppException
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.testcases.schemas import (
    BatchAcceptRequest,
    GenerateRequest,
    ModuleCreateRequest,
    ModuleUpdateRequest,
    TestcaseCreateRequest,
    TestcaseUpdateRequest,
)
from app.modules.testcases.service import (
    create_module,
    create_testcase,
    delete_module,
    delete_testcase,
    export_testcases_xlsx,
    get_module_tree,
    get_template_xlsx,
    get_testcase,
    import_testcases_xlsx,
    list_testcases,
    update_module,
    update_testcase,
)

# 单次导入文件大小硬上限：10MB（5000 行 + 多行步骤典型 < 5MB）
EXCEL_MAX_BYTES = 10 * 1024 * 1024
EXCEL_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _xlsx_response(content: bytes, filename: str) -> StreamingResponse:
    """统一 xlsx 下载响应：UTF-8 文件名 + 防代理压缩。

    ``filename*`` 走 RFC 5987 编码，让中文文件名在 Chrome/Edge/Safari 都能
    正常落盘，不被退化成 ``download.bin``。
    """
    encoded = quote(filename)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=EXCEL_MIME,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"testcases.xlsx\"; "
                f"filename*=UTF-8''{encoded}"
            ),
            "Cache-Control": "no-store",
        },
    )

router = APIRouter(prefix="/api/testcases", tags=["测试用例管理"])


# ════════════════════════════════════════
#  模块树
# ════════════════════════════════════════


@router.get("/projects/{project_id}/modules", response_model=dict)
async def get_modules_tree(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_VIEW)),
):
    """获取项目的测试模块树结构。"""
    tree = await get_module_tree(db, project_id)
    return success_response(data=[node.model_dump(mode="json") for node in tree])


@router.post("/projects/{project_id}/modules", response_model=dict)
async def create_module_endpoint(
    project_id: uuid.UUID,
    data: ModuleCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_CREATE)),
):
    """创建测试模块。"""
    module = await create_module(db, project_id, data)
    return success_response(data=module.model_dump(mode="json"), message="模块创建成功")


@router.patch("/modules/{module_id}", response_model=dict)
async def update_module_endpoint(
    module_id: uuid.UUID,
    data: ModuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_EDIT)),
):
    """更新测试模块。"""
    module = await update_module(db, module_id, data)
    return success_response(data=module.model_dump(mode="json"), message="模块更新成功")


@router.delete("/modules/{module_id}", response_model=dict)
async def delete_module_endpoint(
    module_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_DELETE)),
):
    """删除测试模块（级联删除子模块和用例）。"""
    await delete_module(db, module_id)
    return success_response(message="模块已删除")


# ════════════════════════════════════════
#  测试用例 CRUD
# ════════════════════════════════════════


@router.get("/projects/{project_id}/cases", response_model=dict)
async def list_testcases_endpoint(
    project_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    module_id: uuid.UUID | None = Query(None),
    priority: str | None = Query(None),
    status: str | None = Query(None),
    source: str | None = Query(None),
    exec_result: str | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_VIEW)),
):
    """获取项目下的测试用例列表（支持筛选和分页）。"""
    items, total = await list_testcases(
        db, project_id,
        page=page, page_size=page_size,
        module_id=module_id, priority=priority,
        status=status, source=source,
        exec_result=exec_result, search=search,
    )
    return success_response(data={
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/projects/{project_id}/cases", response_model=dict)
async def create_testcase_endpoint(
    project_id: uuid.UUID,
    data: TestcaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_CREATE)),
):
    """创建测试用例。"""
    testcase = await create_testcase(db, project_id, data, current_user)
    return success_response(data=testcase.model_dump(mode="json"), message="用例创建成功")


@router.get("/cases/{testcase_id}", response_model=dict)
async def get_testcase_endpoint(
    testcase_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_VIEW)),
):
    """获取测试用例详情。"""
    testcase = await get_testcase(db, testcase_id)
    return success_response(data=testcase.model_dump(mode="json"))


@router.patch("/cases/{testcase_id}", response_model=dict)
async def update_testcase_endpoint(
    testcase_id: uuid.UUID,
    data: TestcaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_EDIT)),
):
    """更新测试用例（支持更新步骤）。"""
    testcase = await update_testcase(db, testcase_id, data)
    return success_response(data=testcase.model_dump(mode="json"), message="用例更新成功")


@router.delete("/cases/{testcase_id}", response_model=dict)
async def delete_testcase_endpoint(
    testcase_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_DELETE)),
):
    """删除测试用例。"""
    await delete_testcase(db, testcase_id)
    return success_response(message="用例已删除")


# ════════════════════════════════════════
#  Excel 模板 / 导入 / 导出
# ════════════════════════════════════════


@router.get("/projects/{project_id}/cases/template")
async def download_testcase_template_endpoint(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permissions.TESTCASE_VIEW)),
):
    """下载用例导入模板。

    模板里附带 2 行示例数据 + 一张『字段说明』sheet，让首次接触模板的人不必
    先去翻文档；导出时则使用一致的列结构与中文枚举值，保证"导出 → 改 → 再
    导入覆盖"是无损 round-trip。
    """
    # project_id 走 path 是为了将来按项目分发不同模板（例如各项目自定义优先级
    # 字典）；当前实现里所有项目共用同一份模板，但保留路径占位避免后续 URL
    # 兼容性问题。
    _ = project_id
    raw = get_template_xlsx()
    return _xlsx_response(raw, filename="testcases-template.xlsx")


@router.get("/projects/{project_id}/cases/export")
async def export_testcases_endpoint(
    project_id: uuid.UUID,
    module_id: uuid.UUID | None = Query(None),
    priority: str | None = Query(None),
    status: str | None = Query(None),
    source: str | None = Query(None),
    exec_result: str | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_VIEW)),
):
    """导出当前筛选范围下的全部用例为 xlsx。

    - 与列表 ``list`` 接口共享筛选语义（module_id / priority / status / source
      / exec_result / search），让"页面看到什么 → 导出什么"。
    - 不分页：导出本身就是"拿走全部"。
    """
    raw = await export_testcases_xlsx(
        db, project_id,
        module_id=module_id, priority=priority, status=status,
        source=source, exec_result=exec_result, search=search,
    )
    return _xlsx_response(raw, filename="testcases.xlsx")


@router.post("/projects/{project_id}/cases/import", response_model=dict)
async def import_testcases_endpoint(
    project_id: uuid.UUID,
    file: UploadFile = File(..., description="Excel (.xlsx) 文件"),
    db: AsyncSession = Depends(get_db),
    # TESTCASE_EDIT 即可——既可能新增也可能更新；"创建/更新"两个权限都覆盖
    # 的最小公约数就是 EDIT。viewer 角色没有 EDIT，因此天然挡住。
    current_user: User = Depends(require_permission(Permissions.TESTCASE_EDIT)),
):
    """从 xlsx 文件批量导入用例。

    匹配规则：
    - 行内 ``用例编号`` 为空 → 新增
    - 行内 ``用例编号`` 项目里查不到 → 也按新增处理
    - 行内 ``用例编号`` 项目里能查到 → 全字段覆盖更新（含步骤 / 模块归属 /
      执行结果）

    模块路径用 ``/`` 分层；缺失的层级会自动创建。HTTP 永远 200（除非文件
    本身解析失败，例如不是 xlsx 或缺必需表头）；行级错误聚合在
    ``data.errors`` 里返回，由前端按需展示。
    """
    raw = await file.read()
    if not raw:
        raise AppException("上传的文件为空", code="EMPTY_FILE", status_code=422)
    if len(raw) > EXCEL_MAX_BYTES:
        raise AppException(
            f"文件超过 {EXCEL_MAX_BYTES // (1024 * 1024)}MB 上限",
            code="FILE_TOO_LARGE",
            status_code=413,
        )
    name = (file.filename or "").lower()
    if not name.endswith(".xlsx"):
        raise AppException(
            "请上传 .xlsx 格式的文件（不支持旧版 .xls / 其它格式）",
            code="UNSUPPORTED_FORMAT",
            status_code=415,
        )

    report = await import_testcases_xlsx(db, project_id, raw, current_user)
    summary = (
        f"导入完成：新增 {report.created}、更新 {report.updated}"
        + (f"、跳过 {report.skipped}" if report.skipped else "")
        + (f"，错误 {len(report.errors)}" if report.errors else "")
    )
    return success_response(data=report.model_dump(mode="json"), message=summary)


# ════════════════════════════════════════
#  AI 生成用例
# ════════════════════════════════════════


@router.post("/projects/{project_id}/generate-task", response_model=dict)
async def start_generation_task_endpoint(
    project_id: uuid.UUID,
    data: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_GENERATE)),
):
    """启动真正的后端后台生成任务，刷新/切页不会中断。"""
    from app.modules.testcases.generation_service import start_generation_batch

    batch = await start_generation_batch(db, project_id, data, current_user)
    return success_response(data=batch.model_dump(mode="json"), message="AI 生成任务已启动")


@router.get("/generation-batches/{batch_id}/stream", response_model=None)
async def stream_generation_batch_endpoint(
    batch_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permissions.TESTCASE_GENERATE)),
):
    """订阅后台 AI 生成任务的实时输出流（SSE）。

    - 若任务仍在生成：实时接收 delta / info 事件；
    - 若任务已完成或服务重启后无内存缓冲：从数据库回放并直接发送 done。
    """
    from fastapi.responses import StreamingResponse

    from app.modules.testcases.generation_service import subscribe_batch_stream

    return StreamingResponse(
        subscribe_batch_stream(batch_id, current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{project_id}/generation-batches", response_model=dict)
async def list_generation_batches_endpoint(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_GENERATE)),
):
    """恢复当前用户在项目下的 AI 生成任务（刷新页面后用于悬浮入口）。"""
    from app.modules.testcases.generation_service import list_generation_batches

    batches = await list_generation_batches(db, project_id, current_user)
    return success_response(data=[b.model_dump(mode="json") for b in batches])


@router.get("/generation-batches/{batch_id}", response_model=dict)
async def get_generation_batch_endpoint(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_GENERATE)),
):
    from app.modules.testcases.generation_service import get_generation_batch

    batch = await get_generation_batch(db, batch_id, current_user)
    return success_response(data=batch.model_dump(mode="json"))


@router.post("/generation-batches/{batch_id}/cancel", response_model=dict)
async def cancel_generation_batch_endpoint(
    batch_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permissions.TESTCASE_GENERATE)),
):
    """强制结束一个 AI 生成任务。

    无论任务是真在跑、卡住、还是已被孤立（后端重启留下的 generating 残骸），
    调用本接口都会把它标记为 failed，并通知所有 SSE 订阅者断开。
    """
    from app.modules.testcases.generation_service import cancel_generation_batch

    result = await cancel_generation_batch(batch_id, current_user)
    return success_response(data=result, message="任务已强制结束")


@router.post("/batch-accept", response_model=dict)
async def batch_accept_endpoint(
    data: BatchAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permissions.TESTCASE_APPROVE)),
):
    """批量确认 AI 生成的用例入库。"""
    from app.modules.testcases.generation_service import batch_accept_testcases

    result = await batch_accept_testcases(db, data, current_user)
    return success_response(data=result.model_dump(mode="json"), message="用例已入库")
