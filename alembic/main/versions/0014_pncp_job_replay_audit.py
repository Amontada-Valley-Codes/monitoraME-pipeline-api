"""Adiciona auditoria e contadores de recuperação/replay de jobs PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_pncp_job_replay_audit"
down_revision = "0013_pncp_source_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("replay_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("ingestion_jobs", sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("ingestion_jobs", sa.Column("last_replayed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "ingestion_job_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.BigInteger(), sa.ForeignKey("ingestion_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer()),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_ingestion_job_events_job_created", "ingestion_job_events", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_ingestion_job_events_job_created", table_name="ingestion_job_events")
    op.drop_table("ingestion_job_events")
    op.drop_column("ingestion_jobs", "last_replayed_at")
    op.drop_column("ingestion_jobs", "success_count")
    op.drop_column("ingestion_jobs", "replay_count")
