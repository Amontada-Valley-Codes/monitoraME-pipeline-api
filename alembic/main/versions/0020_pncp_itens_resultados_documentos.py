"""Visoes atuais e historico de itens, resultados e documentos PNCP."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_pncp_recursos"
down_revision = "0019_merge_pncp_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    oportunidade_fk = sa.ForeignKeyConstraint(["source", "external_id"], ["pncp_oportunidades.source", "pncp_oportunidades.external_id"])
    op.create_table("pncp_itens",
        sa.Column("source", sa.Text(), primary_key=True), sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("numero_item", sa.Integer(), primary_key=True), sa.Column("numero_lote", sa.Text()),
        sa.Column("descricao", sa.Text()), sa.Column("quantidade", sa.Text()), sa.Column("unidade", sa.Text()),
        sa.Column("valor_unitario_estimado", sa.Text()), sa.Column("valor_total_estimado", sa.Text()),
        sa.Column("situacao", sa.Text()), sa.Column("criterio_julgamento", sa.Text()),
        sa.Column("resultado_status", sa.Text(), nullable=False, server_default="NAO_CONSULTADO"),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), oportunidade_fk)
    op.create_table("pncp_resultados_itens",
        sa.Column("source", sa.Text(), primary_key=True), sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("numero_item", sa.Integer(), primary_key=True), sa.Column("sequencial_resultado", sa.Integer(), primary_key=True),
        sa.Column("fornecedor", sa.Text()), sa.Column("cnpj_fornecedor", sa.Text()),
        sa.Column("valor_unitario_homologado", sa.Text()), sa.Column("quantidade_homologada", sa.Text()),
        sa.Column("situacao", sa.Text()), sa.Column("data_resultado", sa.Date()),
        sa.Column("disponibilidade", sa.Text(), nullable=False, server_default="ATUAL"),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["source", "external_id", "numero_item"], ["pncp_itens.source", "pncp_itens.external_id", "pncp_itens.numero_item"]))
    op.create_table("pncp_documentos",
        sa.Column("source", sa.Text(), primary_key=True), sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("sequencial_documento", sa.Integer(), primary_key=True), sa.Column("titulo", sa.Text()),
        sa.Column("tipo", sa.Text()), sa.Column("url_oficial", sa.Text(), nullable=False),
        sa.Column("publicado_em", sa.Date()), sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("disponibilidade", sa.Text(), nullable=False, server_default="DISPONIVEL"),
        sa.Column("ausencias_consecutivas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["source", "external_id"], ["pncp_oportunidades.source", "pncp_oportunidades.external_id"]))
    op.create_table("pncp_recurso_versoes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False), sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False), sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("revisao", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False), sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["source", "external_id"], ["pncp_oportunidades.source", "pncp_oportunidades.external_id"]),
        sa.UniqueConstraint("source", "external_id", "resource_type", "resource_key", "revisao", name="uq_pncp_recurso_versao"))
    op.create_index("ix_pncp_recurso_versoes_contratacao", "pncp_recurso_versoes", ["source", "external_id", "resource_type"])
    op.create_index("ix_pncp_documentos_disponibilidade", "pncp_documentos", ["disponibilidade"])


def downgrade() -> None:
    op.drop_index("ix_pncp_documentos_disponibilidade", table_name="pncp_documentos")
    op.drop_index("ix_pncp_recurso_versoes_contratacao", table_name="pncp_recurso_versoes")
    for tabela in ("pncp_recurso_versoes", "pncp_documentos", "pncp_resultados_itens", "pncp_itens"):
        op.drop_table(tabela)
