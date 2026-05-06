"""测试物料模块的 HTTP 路由。

URL 设计（与 PHASE2_DESIGN §5 对齐）：

- ``GET    /api/projects/{project_id}/test-data-sets``         list 物料集（可按 scope 筛）
- ``POST   /api/projects/{project_id}/test-data-sets``         create 物料集
- ``GET    /api/test-data-sets/{set_id}``                      detail（含 items）
- ``PATCH  /api/test-data-sets/{set_id}``                      update 物料集
- ``DELETE /api/test-data-sets/{set_id}``                      delete 物料集（含文件级联）

- ``GET    /api/test-data-sets/{set_id}/items``                list items
- ``POST   /api/test-data-sets/{set_id}/items``                create 非 file item（JSON）
- ``POST   /api/test-data-sets/{set_id}/items/upload``         create file item（multipart）
- ``PATCH  /api/test-data-items/{item_id}``                    update item
- ``DELETE /api/test-data-items/{item_id}``                    delete item

- ``GET    /api/test-data-items/{item_id}/reveal``             读 secret 明文（需 reveal 权限）
- ``GET    /api/test-data-items/{item_id}/file``               下载 file 物料

权限映射：
- 读操作 → ``TEST_DATA_VIEW``
- 写操作（create/update/delete）→ ``TEST_DATA_EDIT``
- reveal → 额外 ``TEST_DATA_REVEAL``（service 层还做"personal owner 放行"补丁）
- 文件上传走 import 也需要 ``TEST_DATA_IMPORT``（与 Task 8.6 的 CSV 导入保持同一权限）

后续 task 补充的端点（本 task 不实现）：
- Task 8.6：/import、/clone、/recommend、/save-as-set
- Task 9.3：/preview-merge、/missing-check
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_permission
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.auth.permissions import Permissions
from app.modules.test_data.schemas import (
    TestDataImportRequest,
    TestDataItemCreateRequest,
    TestDataItemUpdateRequest,
    TestDataMergePreviewRequest,
    TestDataMissingCheckRequest,
    TestDataSaveAsSetRequest,
    TestDataSetCloneRequest,
    TestDataSetCreateRequest,
    TestDataSetUpdateRequest,
)
from app.modules.test_data.service import (
    clone_set,
    create_file_item,
    create_item,
    create_set,
    delete_item,
    delete_set,
    get_set_detail,
    import_csv_to_set,
    import_json_to_set,
    list_items,
    list_sets,
    missing_check,
    preview_merge,
    recommend_sets,
    resolve_file_item,
    reveal_item,
    save_overrides_as_set,
    update_item,
    update_set,
)

router = APIRouter(tags=["测试物料"])


# ─── 物料集（嵌套在 project 下）──────────────────────────────────────


@router.get("/api/projects/{project_id}/test-data-sets")
async def list_sets_endpoint(
    project_id: uuid.UUID,
    scope: str | None = Query(None, description="可选：project / environment / personal"),
    environment_id: uuid.UUID | None = Query(None, description="scope=environment 时按环境过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    items, total = await list_sets(
        db, project_id, user,
        scope=scope, environment_id=environment_id,
        page=page, page_size=page_size,
    )
    return success_response(data={
        "items": [it.model_dump(mode="json") for it in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/api/projects/{project_id}/test-data-sets")
async def create_set_endpoint(
    project_id: uuid.UUID,
    data: TestDataSetCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    detail = await create_set(db, project_id, data, user)
    return success_response(data=detail.model_dump(mode="json"), message="物料集已创建")


# ─── 物料集（按 set_id 单挂）─────────────────────────────────────────


@router.get("/api/test-data-sets/{set_id}")
async def get_set_endpoint(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    detail = await get_set_detail(db, set_id, user)
    return success_response(data=detail.model_dump(mode="json"))


@router.patch("/api/test-data-sets/{set_id}")
async def update_set_endpoint(
    set_id: uuid.UUID,
    data: TestDataSetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    detail = await update_set(db, set_id, data, user)
    return success_response(data=detail.model_dump(mode="json"))


@router.delete("/api/test-data-sets/{set_id}")
async def delete_set_endpoint(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    await delete_set(db, set_id, user)
    return success_response(message="物料集已删除")


# ─── 物料条目 ────────────────────────────────────────────────────────


@router.get("/api/test-data-sets/{set_id}/items")
async def list_items_endpoint(
    set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    items = await list_items(db, set_id, user)
    return success_response(data={"items": [it.model_dump(mode="json") for it in items]})


@router.post("/api/test-data-sets/{set_id}/items")
async def create_item_endpoint(
    set_id: uuid.UUID,
    data: TestDataItemCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    item = await create_item(db, set_id, data, user)
    return success_response(data=item.model_dump(mode="json"), message="物料已添加")


@router.post("/api/test-data-sets/{set_id}/items/upload")
async def upload_file_item_endpoint(
    set_id: uuid.UUID,
    key: Annotated[str, Form(min_length=1, max_length=100)],
    file: Annotated[UploadFile, File(...)],
    description: Annotated[str | None, Form()] = None,
    sort_order: Annotated[int, Form()] = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_IMPORT)),
):
    """上传 file 类型物料。

    使用 multipart/form-data：``key``、``description``、``sort_order`` 走 form 字段；
    ``file`` 走文件部分。与 JSON 创建端点区分开，避免 OpenAPI / 前端库处理混合
    字段时的坑。
    """
    item = await create_file_item(
        db, set_id, key, file, user,
        description=description, sort_order=sort_order,
    )
    return success_response(data=item.model_dump(mode="json"), message="文件已上传")


@router.patch("/api/test-data-items/{item_id}")
async def update_item_endpoint(
    item_id: uuid.UUID,
    data: TestDataItemUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    item = await update_item(db, item_id, data, user)
    return success_response(data=item.model_dump(mode="json"))


@router.delete("/api/test-data-items/{item_id}")
async def delete_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    await delete_item(db, item_id, user)
    return success_response(message="物料已删除")


# ─── 敏感操作：reveal / 文件下载 ────────────────────────────────────


@router.get("/api/test-data-items/{item_id}/reveal")
async def reveal_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # 入口权限：view；service 层再做 reveal-permission / owner 细分
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    """读 secret / text / dataset 的明文值。

    安全策略：
    - 入口要求 ``TEST_DATA_VIEW``（能看列表就能来敲这个 endpoint）
    - service 层再校 ``TEST_DATA_REVEAL`` 或 ``personal owner`` 之一
    - 每次调用写一条结构化审计日志（``logger.info``）
    - 响应体永远不进 chat reasoning / tool log（本 endpoint 是直连 API，
      不经过 LLM，所以从通道上就与 AI 层隔离）
    """
    data = await reveal_item(db, item_id, user)
    return success_response(data=data.model_dump(mode="json"))


@router.get("/api/test-data-items/{item_id}/file")
async def download_file_item_endpoint(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    """下载 file 类型物料。使用 FastAPI ``FileResponse``，支持断点续传。"""
    item, abs_path = await resolve_file_item(db, item_id, user)
    # 以原始 key 命名下载文件（加上扩展名）更直观；key 已经在 schemas 层被限制
    # 为合法字符，直接用安全
    suffix = abs_path.suffix
    return FileResponse(
        path=str(abs_path),
        media_type=item.file_mime or "application/octet-stream",
        filename=f"{item.key}{suffix}",
    )


# ─── Task 8.6：批量导入 / 克隆 / 推荐 / save-as-set ─────────────────


@router.post("/api/test-data-sets/{set_id}/import")
async def import_set_json_endpoint(
    set_id: uuid.UUID,
    data: TestDataImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_IMPORT)),
):
    """JSON 批量导入物料条目。

    成功（含部分失败）均返回 ``ImportReport``，由前端判断 errors 列表长度决定
    是否提示用户；HTTP 永远是 200 除非整体校验不通过（比如 body 结构非法 → 422）。
    """
    report = await import_json_to_set(db, set_id, user, data)
    return success_response(data=report.model_dump(mode="json"), message="导入完成")


@router.post("/api/test-data-sets/{set_id}/import/csv")
async def import_set_csv_endpoint(
    set_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="CSV 文件（UTF-8，首行为表头）")],
    mode: Annotated[str, Form(pattern=r"^(skip_existing|upsert)$")] = "skip_existing",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_IMPORT)),
):
    """CSV 批量导入物料条目（multipart/form-data）。

    - ``file``：CSV 文件（必填），UTF-8 编码，首行表头
    - ``mode``：``skip_existing`` 或 ``upsert``，默认 skip_existing

    CSV 结构性错误（缺必需列 / 非 UTF-8 / 超 10MB）返回 422；行级错误
    聚合到 ``ImportReport.errors`` 里，HTTP 200。
    """
    report = await import_csv_to_set(db, set_id, user, file, mode=mode)
    return success_response(data=report.model_dump(mode="json"), message="CSV 导入完成")


@router.post("/api/test-data-sets/{set_id}/clone")
async def clone_set_endpoint(
    set_id: uuid.UUID,
    data: TestDataSetCloneRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    """把一个物料集连同所有 items 克隆为新集合。"""
    detail = await clone_set(db, set_id, user, data)
    return success_response(data=detail.model_dump(mode="json"), message="物料集已克隆")


@router.get("/api/projects/{project_id}/test-data/recommend")
async def recommend_endpoint(
    project_id: uuid.UUID,
    testcase_ids: Annotated[list[uuid.UUID] | None, Query()] = None,
    environment_id: uuid.UUID | None = Query(None),
    top_n: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    """返回建议随本次执行加载的物料集列表（环境默认 / 项目默认 / 用例默认 / 个人 / 常用）。"""
    items = await recommend_sets(
        db,
        project_id,
        user,
        testcase_ids=testcase_ids,
        environment_id=environment_id,
        top_n=top_n,
    )
    return success_response(data={"items": [it.model_dump(mode="json") for it in items]})


# ─── Task 9.3：preview-merge / missing-check ────────────────────────


@router.post("/api/projects/{project_id}/test-data/preview-merge")
async def preview_merge_endpoint(
    project_id: uuid.UUID,
    body: TestDataMergePreviewRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    """合并预览：根据弹窗里勾选的 sets / 临时覆盖返回最终物料表（secret 遮蔽）。

    幂等只读，但用 POST 因为 body 可能含 ``manual_overrides`` 这种结构化
    数据；GET 拼到 query 上不便。
    """
    payload = body or TestDataMergePreviewRequest()
    detail = await preview_merge(db, project_id, user, payload)
    return success_response(data=detail.model_dump(mode="json"))


@router.post("/api/projects/{project_id}/test-data/missing-check")
async def missing_check_endpoint(
    project_id: uuid.UUID,
    body: TestDataMissingCheckRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_VIEW)),
):
    """缺料预检（非阻断）：返回 ``{missing_keys, will_synthesize, details}``。

    AI 会在执行时通过 ``platform_synthesize_data`` 兜底，故 ``will_synthesize``
    恒为 True；前端只把它当成黄色提示。
    """
    payload = body or TestDataMissingCheckRequest()
    detail = await missing_check(db, project_id, user, payload)
    return success_response(data=detail.model_dump(mode="json"))


@router.post("/api/projects/{project_id}/test-data/save-as-set")
async def save_as_set_endpoint(
    project_id: uuid.UUID,
    data: TestDataSaveAsSetRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(Permissions.TEST_DATA_EDIT)),
):
    """把弹窗里临时改的一批物料沉淀为新物料集。"""
    detail = await save_overrides_as_set(db, project_id, user, data)
    return success_response(data=detail.model_dump(mode="json"), message="已保存为物料集")
