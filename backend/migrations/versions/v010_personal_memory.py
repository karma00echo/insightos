"""Owner-only memory; no historical content backfill or old-table changes."""
import sqlalchemy as sa
from alembic import op

revision = "010_personal_memory"
down_revision = "009_research_reports"


def upgrade():
    # sqlite3 legacy transaction mode does not BEGIN for DDL. Make this revision
    # atomic even when upgrading an already-stamped 009 database.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")
    op.create_table("personal_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_memory_version"),
        sa.CheckConstraint("status IN ('active', 'withdrawn')", name="ck_memory_status"))
    op.create_index("ix_memory_owner_created", "personal_memories", ["owner_id", "created_at", "id"])
    op.create_table("memory_revisions",
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("personal_memories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confirmation", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_memory_revision_version"),
        sa.CheckConstraint("kind IN ('user_fact', 'preference', 'plan')", name="ck_memory_kind"),
        sa.CheckConstraint("confirmation IN ('confirmed', 'revoked')", name="ck_memory_confirmation"),
        sa.CheckConstraint("(confirmation = 'confirmed' AND confirmed_at IS NOT NULL) OR "
                           "(confirmation = 'revoked' AND confirmed_at IS NULL)", name="ck_memory_confirmed_at"))
    op.create_table("memory_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("memory_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_memory_request"),
        sa.CheckConstraint("action IN ('create', 'edit', 'revoke', 'delete')", name="ck_memory_action"))
    op.create_index("ix_memory_operation_resource", "memory_operations", ["owner_id", "memory_id"])


def downgrade():
    raise RuntimeError("Restore a verified isolated backup; memory downgrade is destructive")
