"""Approved private actions. No backfill and no external execution."""

import sqlalchemy as sa
from alembic import op

revision = "012_actions"
down_revision = "011_decisions"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")
    op.create_table(
        "actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_decision_id", sa.String(36)),
        sa.Column("source_version", sa.Integer()),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("cancelled_at", sa.DateTime()),
        sa.CheckConstraint("current_version >= 1", name="ck_action_version"),
        sa.CheckConstraint("status IN ('todo','in_progress','completed','cancelled')", name="ck_action_status"),
        sa.CheckConstraint("(source_decision_id IS NULL) = (source_version IS NULL)", name="ck_action_source"),
        sa.CheckConstraint("source_version IS NULL OR source_version >= 1", name="ck_action_source_version"),
    )
    op.create_index("ix_action_owner_state", "actions", ["owner_id", "status", "created_at", "id"])
    op.create_index("ix_action_source", "actions", ["source_decision_id"])
    op.create_table(
        "action_revisions",
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", sa.JSON(none_as_null=True)),
        sa.Column("due_date", sa.Date()),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_action_revision_version"),
        sa.CheckConstraint("status IN ('todo','in_progress','completed','cancelled')", name="ck_action_revision_status"),
        sa.CheckConstraint("operation IN ('create','edit','status')", name="ck_action_revision_operation"),
    )
    op.create_table(
        "action_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("action_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_action_request"),
        sa.CheckConstraint("operation IN ('create','edit','status','delete')", name="ck_action_operation"),
    )
    op.create_index("ix_action_mutation_resource", "action_mutations", ["owner_id", "action_id"])


def downgrade():
    # Match T03/T04: never silently destroy approved private records.
    raise RuntimeError("Restore a verified backup; action downgrade is destructive")
