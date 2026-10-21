"""Registra o contexto seguro de falhas nos jobs PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_pncp_job_error_context"
down_revision = "0011_pncp_sync_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("last_error_context", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "last_error_context")
