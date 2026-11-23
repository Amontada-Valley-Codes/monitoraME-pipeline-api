"""Persist a stable opportunity snapshot and progress for matching blocks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0034_nlp_matching_blocks"
down_revision = "0033_pncp_historico"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nlp_matching_batches", sa.Column("opportunity_keys", postgresql.JSONB(), nullable=True))
    op.add_column(
        "nlp_matching_batches",
        sa.Column("matching_offsets", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("nlp_matching_batches", sa.Column("matching_model_version", sa.Text(), nullable=True))
    op.add_column("nlp_matching_batches", sa.Column("matching_taxonomy_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("nlp_matching_batches", "matching_taxonomy_version")
    op.drop_column("nlp_matching_batches", "matching_model_version")
    op.drop_column("nlp_matching_batches", "matching_offsets")
    op.drop_column("nlp_matching_batches", "opportunity_keys")
