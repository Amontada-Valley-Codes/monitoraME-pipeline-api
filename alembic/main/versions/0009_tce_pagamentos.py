"""add validated TCE payment publications

Revision ID: 0009_tce_pagamentos
Revises: 0008_tce_methodology
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_tce_pagamentos"
down_revision = "0008_tce_methodology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_pagamento_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("codigo_municipio_ibge", sa.Text(), nullable=False),
        sa.Column("municipio", sa.Text(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("competencia_corte", sa.Text(), nullable=False),
        sa.Column("versao_metodologica", sa.Text(), server_default=sa.text("'tce-pagamentos-v1'"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("qualidade_status", sa.Text(), nullable=False),
        sa.Column("resposta", postgresql.JSONB(), nullable=False),
        sa.Column("problemas", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("substituido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('EM_VALIDACAO','PUBLICADO','SUBSTITUIDO','BLOQUEADO','FALHOU')", name="ck_tce_pagamento_runs_status"),
        sa.CheckConstraint("qualidade_status IN ('APROVADA','PARCIAL','BLOQUEADA')", name="ck_tce_pagamento_runs_qualidade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tce_pagamento_runs_escopo", "tce_pagamento_runs", ["codigo_municipio_tce", "exercicio_orcamento"])
    op.create_index(
        "uq_tce_pagamento_run_publicado_escopo",
        "tce_pagamento_runs",
        ["codigo_municipio_tce", "exercicio_orcamento", "competencia_corte"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLICADO'"),
    )

    op.create_table(
        "tce_orgaos_staging",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("nome_orgao", sa.Text(), nullable=False),
        sa.Column("administracao", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("administracao IN ('PREFEITURA','CAMARA')", name="ck_tce_orgaos_administracao"),
        sa.ForeignKeyConstraint(["run_id"], ["tce_pagamento_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "codigo_orgao", name="uq_tce_orgaos_run_codigo"),
    )
    op.create_table(
        "tce_pagamentos_staging",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("chave_pagamento", sa.Text(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("competencia_documento", sa.Text(), nullable=False),
        sa.Column("data_pagamento", sa.Date(), nullable=False),
        sa.Column("valor_pagamento_centavos", sa.BigInteger(), nullable=False),
        sa.Column("estado_de_estornado", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["tce_pagamento_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chave_pagamento", name="uq_tce_pagamentos_staging_run_chave"),
    )
    op.create_table(
        "tce_pagamentos_conciliados",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("chave_pagamento", sa.Text(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("codigo_elemento_despesa", sa.Text(), nullable=False),
        sa.Column("data_pagamento", sa.Date(), nullable=False),
        sa.Column("valor_pagamento_centavos", sa.BigInteger(), nullable=False),
        sa.Column("documento_fornecedor", sa.Text(), nullable=True),
        sa.Column("cnpj_fornecedor", sa.Text(), nullable=True),
        sa.Column("raiz_cnpj_fornecedor", sa.Text(), nullable=True),
        sa.Column("cpf_fornecedor", sa.Text(), nullable=True),
        sa.Column("categoria_fornecedor", sa.Text(), nullable=False),
        sa.Column("classificacao_cadastral", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.CheckConstraint("categoria_fornecedor IN ('ME','EPP','DEMAIS','NAO_IDENTIFICADO','CPF','DOCUMENTO_INVALIDO')", name="ck_tce_pagamentos_categoria"),
        sa.ForeignKeyConstraint(["run_id"], ["tce_pagamento_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chave_pagamento", name="uq_tce_pagamentos_conciliados_run_chave"),
    )
    op.create_index("idx_tce_pagamentos_conciliados_run", "tce_pagamentos_conciliados", ["run_id"])
    op.create_index("idx_tce_pagamentos_conciliados_elemento", "tce_pagamentos_conciliados", ["codigo_elemento_despesa"])
    op.create_table(
        "tce_pagamento_paginas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("competencia", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("start_index", sa.Integer(), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["tce_pagamento_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "endpoint", "competencia", "start_index", name="uq_tce_pagamento_paginas_identidade"),
    )


def downgrade() -> None:
    op.drop_table("tce_pagamento_paginas")
    op.drop_index("idx_tce_pagamentos_conciliados_elemento", table_name="tce_pagamentos_conciliados")
    op.drop_index("idx_tce_pagamentos_conciliados_run", table_name="tce_pagamentos_conciliados")
    op.drop_table("tce_pagamentos_conciliados")
    op.drop_table("tce_pagamentos_staging")
    op.drop_table("tce_orgaos_staging")
    op.drop_index("uq_tce_pagamento_run_publicado_escopo", table_name="tce_pagamento_runs")
    op.drop_index("idx_tce_pagamento_runs_escopo", table_name="tce_pagamento_runs")
    op.drop_table("tce_pagamento_runs")
