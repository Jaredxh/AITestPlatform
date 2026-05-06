from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


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
    return JSONResponse(
        status_code=500,
        content={"success": False, "data": None, "message": "服务器内部错误", "code": "INTERNAL_ERROR"},
    )
