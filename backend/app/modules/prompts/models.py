import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    sub_category: Mapped[str | None] = mapped_column(String(50))

    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    variables: Mapped[list | None] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )

    project = relationship("Project", lazy="selectin")
    creator = relationship("User", lazy="selectin")
    versions: Mapped[list["PromptVersion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", lazy="noload",
        order_by="PromptVersion.version.desc()",
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )

    template: Mapped["PromptTemplate"] = relationship(back_populates="versions")
    creator = relationship("User", lazy="selectin")
