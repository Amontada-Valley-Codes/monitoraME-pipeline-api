"""Evidencias de qualidade semantica PNCP por RAW e regra."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_pncp_qualidade"
down_revision = "0016_pncp_oportunidades_matching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pncp_oportunidades", sa.Column("data_quality_status", sa.Text(), nullable=False, server_default="not_evaluated"))
    op.add_column("pncp_oportunidades", sa.Column("dados_semanticos", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_table("pncp_avaliacoes_qualidade",
        sa.Column("raw_record_id", sa.BigInteger(), sa.ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("rule_version", sa.Text(), primary_key=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text()),
        sa.Column("data_quality_status", sa.Text(), nullable=False),
        sa.Column("decisao", sa.Text(), nullable=False, server_default="AVALIADO"),
        sa.Column("campos", postgresql.JSONB(), nullable=False),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("avaliado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_index("ix_pncp_avaliacoes_qualidade_endpoint", "pncp_avaliacoes_qualidade", ["endpoint"])
    op.create_index("ix_pncp_avaliacoes_qualidade_run_id", "pncp_avaliacoes_qualidade", ["run_id"])


def downgrade() -> None:
    op.drop_table("pncp_avaliacoes_qualidade")
    op.drop_column("pncp_oportunidades", "dados_semanticos")
    op.drop_column("pncp_oportunidades", "data_quality_status")
