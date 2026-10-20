"""Cria historico RAW PNCP e jobs derivados de normalizacao."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_pncp_raw_records"
down_revision = "0007_portes_mensais"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "raw_pncp_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("run_id", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "entity_type", "external_id", "payload_hash", name="uq_raw_pncp_record_version"),
    )
    op.create_index("idx_raw_pncp_records_entidade_identificador", "raw_pncp_records", ["source", "entity_type", "external_id"])
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.Text(), nullable=False), sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False), sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("raw_record_id", sa.BigInteger(), sa.ForeignKey("raw_pncp_records.id", ondelete="RESTRICT")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("window_start", sa.DateTime(timezone=True)), sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("page", sa.Integer()), sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("locked_by", sa.Text()), sa.Column("lease_token", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'QUARANTINED')", name="ck_ingestion_jobs_status"),
    )
    op.create_index("uq_ingestion_jobs_dedupe_ativo", "ingestion_jobs", ["dedupe_key"], unique=True, postgresql_where=sa.text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"))
    op.create_index("idx_ingestion_jobs_agendamento", "ingestion_jobs", ["status", "next_run_at", "priority"])
    op.create_index("idx_ingestion_jobs_lease", "ingestion_jobs", ["status", "lease_until"])


def downgrade():
    op.drop_table("ingestion_jobs")
    op.drop_table("raw_pncp_records")
