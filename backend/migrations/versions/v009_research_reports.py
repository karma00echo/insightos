"""Add immutable report versions without changing historical research or evidence."""
import sqlalchemy as sa
from alembic import op

revision = "009_research_reports"
down_revision = "008_research"


def upgrade():
    op.create_table("research_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("research_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"), sa.UniqueConstraint("task_id", "version"))
    op.create_index("ix_research_reports_user_id", "research_reports", ["user_id"])
    op.create_index("ix_research_reports_task_id", "research_reports", ["task_id"])


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
