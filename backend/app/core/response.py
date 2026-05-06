from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ResponseSchema(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    message: str | None = None
    code: str | None = None


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def success_response(data: Any = None, message: str | None = None) -> dict:
    return {"success": True, "data": data, "message": message}


def error_response(message: str, code: str | None = None) -> dict:
    return {"success": False, "data": None, "message": message, "code": code}
