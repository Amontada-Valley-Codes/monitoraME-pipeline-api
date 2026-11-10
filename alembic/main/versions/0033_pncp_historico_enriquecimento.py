"""Persist PNCP procurement event history in enrichment cache."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0033_pncp_historico"
down_revision = "0032_pncp_job_query_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pncp_enriquecimentos",
        sa.Column("history_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("pncp_enriquecimentos", sa.Column("history_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pncp_enriquecimentos", "history_hash")
    op.drop_column("pncp_enriquecimentos", "history_payload")
