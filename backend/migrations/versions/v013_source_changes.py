"""Reference-only change detection ledger. No backfill."""

import sqlalchemy as sa
from alembic import op

revision = "013_source_changes"
down_revision = "012_actions"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")
    op.create_table(
        "source_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("old_id", sa.String(36), nullable=False),
        sa.Column("old_version", sa.String(36), nullable=False),
        sa.Column("new_id", sa.String(36), nullable=False),
        sa.Column("new_version", sa.String(36), nullable=False),
        sa.Column("detected_state", sa.String(16), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "kind", "old_id", "old_version", "new_id", "new_version", name="uq_source_change_pair"),
        sa.CheckConstraint("kind IN ('document','memory')", name="ck_source_change_kind"),
        sa.CheckConstraint("detected_state IN ('changed','unchanged','incomparable')", name="ck_source_change_state"),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; source change downgrade is destructive")
