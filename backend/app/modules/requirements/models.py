import uuid

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RequirementDocument(Base):
    __tablename__ = "requirement_documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="parsed", server_default="parsed"
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    project = relationship("Project", lazy="selectin")
    uploader = relationship("User", lazy="selectin")
    reviews: Mapped[list["AIReview"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin",
        order_by="AIReview.created_at.desc()",
    )


class AIReview(Base):
    __tablename__ = "ai_reviews"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirement_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False,
    )
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_configs.id"), nullable=True,
    )
    model_used: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending",
    )
    overall_score: Mapped[float | None] = mapped_column(Float)
    dimensions: Mapped[dict | None] = mapped_column(JSON)
    issues: Mapped[list | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(Text)
    review_time_ms: Mapped[int | None] = mapped_column(Integer)

    document: Mapped["RequirementDocument"] = relationship(back_populates="reviews")
    reviewer = relationship("User", lazy="selectin")
    llm_config = relationship("LLMConfig", lazy="selectin")
