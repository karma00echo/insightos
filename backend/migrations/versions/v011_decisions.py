"""Private decisions, revision-bound basis and replay protection. No backfill."""

import sqlalchemy as sa
from alembic import op

revision = "011_decisions"
down_revision = "010_personal_memory"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN")
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("current_version >= 1", name="ck_decision_version"),
        sa.CheckConstraint("status IN ('active','withdrawn')", name="ck_decision_status"),
    )
    op.create_index("ix_decision_owner_state", "decisions", ["owner_id", "status", "created_at", "id"])
    op.create_table(
        "decision_revisions",
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_decision_revision_version"),
        sa.CheckConstraint("status IN ('active','withdrawn')", name="ck_decision_revision_status"),
        sa.CheckConstraint(
            "(status='active' AND confirmed_at IS NOT NULL) OR (status='withdrawn' AND confirmed_at IS NULL)",
            name="ck_decision_confirmation",
        ),
    )
    op.create_table(
        "decision_basis",
        sa.Column("decision_id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reference", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id", "version"],
            ["decision_revisions.decision_id", "decision_revisions.version"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('user_input','memory','user_message','document_chunk')", name="ck_decision_basis_kind"
        ),
    )
    op.create_table(
        "decision_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("decision_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="uq_decision_request"),
        sa.CheckConstraint("action IN ('create','edit','revoke','delete')", name="ck_decision_action"),
    )
    op.create_index("ix_decision_mutation_resource", "decision_mutations", ["owner_id", "decision_id"])


def downgrade():
    raise RuntimeError("Restore a verified backup; decision downgrade is destructive")
