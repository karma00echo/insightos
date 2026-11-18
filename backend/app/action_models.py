"""User-approved tasks, private immutable revisions and body-free replay tombstones."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow

STATES = "status IN ('todo','in_progress','completed','cancelled')"


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_action_version"),
        CheckConstraint(STATES, name="ck_action_status"),
        CheckConstraint("(source_decision_id IS NULL) = (source_version IS NULL)", name="ck_action_source"),
        CheckConstraint("source_version IS NULL OR source_version >= 1", name="ck_action_source_version"),
        Index("ix_action_owner_state", "owner_id", "status", "created_at", "id"),
        Index("ix_action_source", "source_decision_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    current_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    source_decision_id: Mapped[str | None] = mapped_column(String(36))
    source_version: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    approved_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)


class ActionRevision(Base):
    __tablename__ = "action_revisions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_action_revision_version"),
        CheckConstraint(STATES, name="ck_action_revision_status"),
        CheckConstraint("operation IN ('create','edit','status')", name="ck_action_revision_operation"),
    )
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    due_date: Mapped[datetime | None] = mapped_column(Date)
    operation: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActionMutation(Base):
    __tablename__ = "action_mutations"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_action_request"),
        Index("ix_action_mutation_resource", "owner_id", "action_id"),
        CheckConstraint("operation IN ('create','edit','status','delete')", name="ck_action_operation"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(36))
    action_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
