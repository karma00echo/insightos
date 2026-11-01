"""Durable bounded research tasks; existing sources and indexes are untouched."""
import sqlalchemy as sa
from alembic import op

revision = "008_research"
down_revision = "007_chunk_locator"


def upgrade():
    op.create_table("research_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active_user_id", sa.String(36), unique=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("phase", sa.String(30), nullable=False),
        *[sa.Column(name, sa.JSON(), nullable=False) for name in ("plan", "steps", "evidence", "events", "usage")],
        sa.Column("result", sa.JSON()), sa.Column("error", sa.Text()),
        sa.Column("lease_token", sa.String(36)), sa.Column("deadline_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"))
    op.create_index("ix_research_tasks_user_id", "research_tasks", ["user_id"])
    op.create_index("ix_research_tasks_status", "research_tasks", ["status"])


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
