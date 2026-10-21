"""Armazena enriquecimento opcional de contratações PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_pncp_enriquecimento"
down_revision = "0014_pncp_job_replay_audit"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "pncp_enriquecimentos",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("cnpj_orgao", sa.Text(), nullable=False),
        sa.Column("ano_compra", sa.Integer(), nullable=False),
        sa.Column("sequencial_compra", sa.Integer(), nullable=False),
        sa.Column("source_version_hash", sa.Text(), nullable=False),
        sa.Column("detail_payload", postgresql.JSONB()), sa.Column("detail_hash", sa.Text()),
        sa.Column("items_payload", postgresql.JSONB()), sa.Column("items_hash", sa.Text()),
        sa.Column("documents_payload", postgresql.JSONB()), sa.Column("documents_hash", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_pncp_enriquecimentos_expiracao", "pncp_enriquecimentos", ["expires_at"])

def downgrade() -> None:
    op.drop_index("idx_pncp_enriquecimentos_expiracao", table_name="pncp_enriquecimentos")
    op.drop_table("pncp_enriquecimentos")
