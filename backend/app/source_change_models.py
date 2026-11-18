"""Reference-only, owner-private on-demand detection ledger. Never stores source text."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow


class SourceChange(Base):
    __tablename__ = "source_changes"
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "old_id", "old_version", "new_id", "new_version", name="uq_source_change_pair"),
        CheckConstraint("kind IN ('document','memory')", name="ck_source_change_kind"),
        CheckConstraint("detected_state IN ('changed','unchanged','incomparable')", name="ck_source_change_state"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    old_id: Mapped[str] = mapped_column(String(36))
    old_version: Mapped[str] = mapped_column(String(36))
    new_id: Mapped[str] = mapped_column(String(36))
    new_version: Mapped[str] = mapped_column(String(36))
    detected_state: Mapped[str] = mapped_column(String(16))
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
