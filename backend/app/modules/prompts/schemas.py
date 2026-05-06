import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VariableDeclaration(BaseModel):
    name: str
    label: str
    source: str = Field("manual", pattern=r"^(context|auto|manual)$")


class PromptCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    content: str = Field(..., min_length=1)
    category: str = Field(..., pattern=r"^(chat|review|generation|ui_test|custom)$")
    sub_category: str | None = None
    is_default: bool = False
    auto_apply: bool = False
    variables: list[VariableDeclaration] = Field(default_factory=list)


class PromptUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    content: str | None = Field(None, min_length=1)
    category: str | None = Field(None, pattern=r"^(chat|review|generation|ui_test|custom)$")
    sub_category: str | None = None
    is_default: bool | None = None
    auto_apply: bool | None = None
    variables: list[VariableDeclaration] | None = None
    change_note: str | None = Field(None, max_length=500)


class PromptResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    content: str
    category: str
    sub_category: str | None = None
    is_system: bool
    is_default: bool
    auto_apply: bool
    variables: list[Any] | None = None
    version: int
    created_by: uuid.UUID
    creator_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PromptListResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    category: str
    sub_category: str | None = None
    is_system: bool
    is_default: bool
    auto_apply: bool
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PromptVersionResponse(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    version: int
    content: str
    change_note: str | None = None
    created_by: uuid.UUID
    creator_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PromptRenderRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
