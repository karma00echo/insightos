"""Append-only body-free audit. Decision deletion cascades; no derived text."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow


class ImpactReviewEvent(Base):
    __tablename__ = 'impact_review_events'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'sequence', name='uq_impact_sequence'),
        UniqueConstraint('owner_id', 'request_id', name='uq_impact_request'),
        CheckConstraint('sequence >= 1 AND decision_version >= 1', name='ck_impact_versions'),
        CheckConstraint("status IN ('pending','viewed','needs_review','keep','revise')", name='ck_impact_status'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    decision_id: Mapped[str] = mapped_column(ForeignKey('decisions.id', ondelete='CASCADE'))
    decision_version: Mapped[int] = mapped_column(Integer)
    change_id: Mapped[str] = mapped_column(ForeignKey('source_changes.id', ondelete='CASCADE'))
    analysis_id: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
