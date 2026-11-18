"""Private memory storage, intentionally separate from knowledge and decisions."""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow


class PersonalMemory(Base):
    __tablename__ = "personal_memories"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_memory_version"),
        CheckConstraint("status IN ('active', 'withdrawn')", name="ck_memory_status"),
        Index("ix_memory_owner_created", "owner_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_memory_revision_version"),
        CheckConstraint("kind IN ('user_fact', 'preference', 'plan')", name="ck_memory_kind"),
        CheckConstraint("confirmation IN ('confirmed', 'revoked')", name="ck_memory_confirmation"),
        CheckConstraint("(confirmation = 'confirmed' AND confirmed_at IS NOT NULL) OR "
                        "(confirmation = 'revoked' AND confirmed_at IS NULL)", name="ck_memory_confirmed_at"),
    )
    memory_id: Mapped[str] = mapped_column(ForeignKey("personal_memories.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    confirmation: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MemoryOperation(Base):
    """Private idempotency ledger; no response bodies, source text or reasons."""
    __tablename__ = "memory_operations"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_memory_request"),
        Index("ix_memory_operation_resource", "owner_id", "memory_id"),
        CheckConstraint("action IN ('create', 'edit', 'revoke', 'delete')", name="ck_memory_action"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(36))
    memory_id: Mapped[str] = mapped_column(String(36))  # deliberately survives hard delete
    action: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
