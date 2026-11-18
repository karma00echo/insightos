"""Decision state, immutable revisions, typed basis and private replay ledger."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_decision_version"),
        CheckConstraint("status IN ('active','withdrawn')", name="ck_decision_status"),
        Index("ix_decision_owner_state", "owner_id", "status", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    current_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DecisionRevision(Base):
    __tablename__ = "decision_revisions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_decision_revision_version"),
        CheckConstraint("status IN ('active','withdrawn')", name="ck_decision_revision_status"),
        CheckConstraint(
            "(status='active' AND confirmed_at IS NOT NULL) OR (status='withdrawn' AND confirmed_at IS NULL)",
            name="ck_decision_confirmation",
        ),
    )
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DecisionBasis(Base):
    __tablename__ = "decision_basis"
    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "version"],
            ["decision_revisions.decision_id", "decision_revisions.version"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('user_input','memory','user_message','document_chunk')", name="ck_decision_basis_kind"
        ),
    )
    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    reference: Mapped[dict] = mapped_column(JSON)


class DecisionMutation(Base):
    __tablename__ = "decision_mutations"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_decision_request"),
        Index("ix_decision_mutation_resource", "owner_id", "decision_id"),
        CheckConstraint("action IN ('create','edit','revoke','delete')", name="ck_decision_action"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(36))
    decision_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
