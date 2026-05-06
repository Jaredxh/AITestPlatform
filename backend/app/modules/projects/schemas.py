import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    status: str | None = Field(None, pattern=r"^(active|archived)$")


class MemberAddRequest(BaseModel):
    user_id: uuid.UUID
    role: str = Field("member", pattern=r"^(admin|member|viewer)$")


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str | None
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    owner_id: uuid.UUID
    owner_name: str | None = None
    member_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetailResponse(ProjectResponse):
    members: list[MemberResponse] = []
