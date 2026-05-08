import logging
import traceback

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)


class AppException(Exception):
    def __init__(self, message: str, code: str = "ERROR", status_code: int = 400):
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFoundException(AppException):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message=message, code="NOT_FOUND", status_code=404)


class PermissionDeniedException(AppException):
    def __init__(self, message: str = "权限不足"):
        super().__init__(message=message, code="PERMISSION_DENIED", status_code=403)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "未认证"):
        super().__init__(message=message, code="UNAUTHORIZED", status_code=401)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """业务异常统一出口。

    顺带把 4xx 也打到日志（INFO 级，不当 error 喧哗），方便后续仅凭 docker logs
    就能复现"哪个接口为什么 400"。曾经因为前端 toast 把后端 ``message`` 吃掉，
    用户只看到 "Bad Request"，靠 SQL 噪声反查浪费时间。
    """
    logger.info(
        "AppException %s %s -> %d %s: %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.code,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "message": exc.message, "code": exc.code},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "message": exc.detail, "code": "HTTP_ERROR"},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底 500：始终把真实 traceback 写到 backend 日志。

    - 历史 bug：原实现把 ``Exception`` 静默转成"服务器内部错误"且既不打日志、
      也不返回任何细节，导致客户端报 500 时根本无法定位（前端只看到生成的 502
      壳子，docker logs 也是干净的）。
    - 现在：``logger.exception`` 走 ``app`` logger，``main.py`` 已把它
      接到 stdout，``docker compose logs backend`` 直接能看到完整 trace；
    - DEBUG 模式（``DEBUG=true``，本地排查用）额外把异常类型 + 一段 trace
      回写进响应体的 ``debug`` 字段，省去翻日志这一步——生产 ``DEBUG=false``
      时这条不会暴露内部细节。
    """
    method = request.method
    path = request.url.path
    logger.exception("Unhandled exception on %s %s: %s", method, path, exc)

    body: dict = {
        "success": False,
        "data": None,
        "message": "服务器内部错误",
        "code": "INTERNAL_ERROR",
    }
    if settings.DEBUG:
        body["debug"] = {
            "exception": f"{type(exc).__name__}: {exc}",
            # 截到 4KB 防止前端 devtools 卡死；完整 trace 还是去日志看
            "traceback": "".join(traceback.format_exception(exc))[-4000:],
        }
    return JSONResponse(status_code=500, content=body)
