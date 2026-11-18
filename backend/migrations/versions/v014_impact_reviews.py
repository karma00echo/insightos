"""Body-free impact review audit, no backfill."""
import sqlalchemy as sa
from alembic import op

revision = '014_impact_reviews'
down_revision = '013_source_changes'


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite' and not bind.connection.driver_connection.in_transaction:
        bind.exec_driver_sql('BEGIN')
    op.create_table(
        'impact_review_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision_id', sa.String(36), sa.ForeignKey('decisions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('decision_version', sa.Integer(), nullable=False),
        sa.Column('change_id', sa.String(36), sa.ForeignKey('source_changes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('analysis_id', sa.String(36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('analysis_id', 'sequence', name='uq_impact_sequence'),
        sa.UniqueConstraint('owner_id', 'request_id', name='uq_impact_request'),
        sa.CheckConstraint('sequence >= 1 AND decision_version >= 1', name='ck_impact_versions'),
        sa.CheckConstraint("status IN ('pending','viewed','needs_review','keep','revise')", name='ck_impact_status'),
    )


def downgrade():
    raise RuntimeError('Restore a verified backup; review downgrade is destructive')
