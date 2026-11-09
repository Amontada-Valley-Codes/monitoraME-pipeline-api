"""Persist the effective PNCP query parameters and configuration version."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0032_pncp_job_query_contract"
down_revision = "0031_tce_despesas_calculadas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("ingestion_jobs", sa.Column("config_version", sa.Text()))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "config_version")
    op.drop_column("ingestion_jobs", "parameters")
