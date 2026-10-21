"""Visao normalizada e quarentena de contratacoes PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0010_pncp_normalizacao"
down_revision = "0009_pncp_atualizacao"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("pncp_oportunidades", 
    sa.Column("source", sa.Text(), primary_key=True), 
    sa.Column("external_id", sa.Text(), primary_key=True), 
    sa.Column("raw_record_id", sa.BigInteger(), sa.ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"), nullable=False), 
    sa.Column("payload_hash", sa.Text(), nullable=False), 
    sa.Column("source_updated_at", sa.DateTime(timezone=True)), 
    sa.Column("orgao", postgresql.JSONB()), sa.Column("unidade", postgresql.JSONB()), sa.Column("objeto", sa.Text()), 
    sa.Column("modalidade", postgresql.JSONB()), 
    sa.Column("situacao", postgresql.JSONB()), 
    sa.Column("prazo_proposta_final", sa.DateTime(timezone=True)), 
    sa.Column("url_oficial", sa.Text(), nullable=False), 
    sa.Column("normalized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_index("idx_pncp_oportunidades_prazo", "pncp_oportunidades", ["prazo_proposta_final"])
    op.create_table("pncp_quarentena", 
    sa.Column("raw_record_id", sa.BigInteger(), sa.ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"), primary_key=True), 
    sa.Column("motivo", sa.Text(), nullable=False), 
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
def downgrade():
    op.drop_table("pncp_quarentena"); op.drop_table("pncp_oportunidades")
