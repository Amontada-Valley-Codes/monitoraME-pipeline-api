"""Persist calculated TCE expenses with immutable history.

Revision ID: 0031_tce_despesas_calculadas
Revises: 0030_api_response_cache_sem_ttl
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0031_tce_despesas_calculadas"
down_revision = "0030_api_response_cache_sem_ttl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_despesa_calculo_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("escopo_chave", sa.Text(), nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("algoritmo_versao", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_anterior_id", sa.BigInteger(), nullable=True),
        sa.Column("quantidade_empenhos", sa.Integer(), nullable=False),
        sa.Column("quantidade_anulacoes", sa.Integer(), nullable=False),
        sa.Column("quantidade_pagamentos", sa.Integer(), nullable=False),
        sa.Column(
            "fontes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("substituido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PUBLICADO', 'SUBSTITUIDO')",
            name="ck_tce_despesa_calculo_runs_status",
        ),
        sa.CheckConstraint(
            "quantidade_empenhos >= 0 AND quantidade_anulacoes >= 0 "
            "AND quantidade_pagamentos >= 0",
            name="ck_tce_despesa_calculo_runs_quantidades",
        ),
        sa.CheckConstraint(
            "exercicio_orcamento > 0 AND exercicio_orcamento % 100 = 0",
            name="ck_tce_despesa_calculo_runs_exercicio",
        ),
        sa.CheckConstraint(
            "length(input_hash) = 64",
            name="ck_tce_despesa_calculo_runs_hash",
        ),
        sa.ForeignKeyConstraint(
            ["run_anterior_id"],
            ["tce_despesa_calculo_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_tce_despesa_calculo_run_publicado_escopo",
        "tce_despesa_calculo_runs",
        ["escopo_chave"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLICADO'"),
    )
    op.create_index(
        "idx_tce_despesa_calculo_runs_escopo_historico",
        "tce_despesa_calculo_runs",
        ["escopo_chave", "publicado_em"],
    )
    op.create_index(
        "idx_tce_despesa_calculo_runs_consulta",
        "tce_despesa_calculo_runs",
        ["codigo_municipio_tce", "exercicio_orcamento", "status"],
    )

    op.create_table(
        "tce_despesas_calculadas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("registro_pai_id", sa.BigInteger(), nullable=True),
        sa.Column("natureza", sa.Text(), nullable=False),
        sa.Column("chave_registro", sa.Text(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("conteudo_hash", sa.Text(), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("competencia_documento", sa.Text(), nullable=False),
        sa.Column("competencia_analitica", sa.Text(), nullable=False),
        sa.Column("data_referencia", sa.Date(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("codigo_municipio_ibge", sa.Text(), nullable=True),
        sa.Column("municipio", sa.Text(), nullable=False),
        sa.Column("uf", sa.Text(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("nome_orgao", sa.Text(), nullable=True),
        sa.Column("codigo_unidade_orcamentaria", sa.Text(), nullable=False),
        sa.Column("tipo_documento_fornecedor", sa.Text(), nullable=True),
        sa.Column("documento_fornecedor", sa.Text(), nullable=True),
        sa.Column("cnpj_fornecedor", sa.Text(), nullable=True),
        sa.Column("cpf_fornecedor", sa.Text(), nullable=True),
        sa.Column("nome_fornecedor", sa.Text(), nullable=True),
        sa.Column("municipio_fornecedor", sa.Text(), nullable=True),
        sa.Column("uf_fornecedor", sa.Text(), nullable=True),
        sa.Column("porte_fornecedor", sa.Text(), nullable=False),
        sa.Column("fonte_porte", sa.Text(), nullable=True),
        sa.Column("porte_observado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("natureza_despesa_codigo", sa.Text(), nullable=False),
        sa.Column("natureza_despesa_descricao", sa.Text(), nullable=True),
        sa.Column("valor_centavos", sa.BigInteger(), nullable=False),
        sa.Column("valor_empenhado_centavos", sa.BigInteger(), nullable=True),
        sa.Column("valor_anulado_centavos", sa.BigInteger(), nullable=True),
        sa.Column("valor_liquido_centavos", sa.BigInteger(), nullable=True),
        sa.Column(
            "atributos",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criado_em",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "natureza IN ('EMPENHO', 'ANULACAO_EMPENHO', 'PAGAMENTO')",
            name="ck_tce_despesas_calculadas_natureza",
        ),
        sa.CheckConstraint(
            "porte_fornecedor IN ('MEI', 'ME', 'EPP', 'DEMAIS', 'NAO_IDENTIFICADO')",
            name="ck_tce_despesas_calculadas_porte",
        ),
        sa.CheckConstraint(
            "valor_centavos >= 0",
            name="ck_tce_despesas_calculadas_valor",
        ),
        sa.CheckConstraint(
            "competencia_documento ~ '^[0-9]{4}-(0[1-9]|1[0-2])$' "
            "AND competencia_analitica ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_tce_despesas_calculadas_competencias",
        ),
        sa.CheckConstraint(
            "exercicio_orcamento > 0 AND exercicio_orcamento % 100 = 0",
            name="ck_tce_despesas_calculadas_exercicio",
        ),
        sa.CheckConstraint(
            "length(conteudo_hash) = 64",
            name="ck_tce_despesas_calculadas_hash",
        ),
        sa.CheckConstraint(
            "(natureza = 'EMPENHO' "
            "AND registro_pai_id IS NULL "
            "AND valor_empenhado_centavos IS NOT NULL "
            "AND valor_anulado_centavos IS NOT NULL "
            "AND valor_liquido_centavos IS NOT NULL "
            "AND valor_empenhado_centavos >= 0 "
            "AND valor_anulado_centavos >= 0 "
            "AND valor_liquido_centavos = valor_empenhado_centavos - valor_anulado_centavos "
            "AND valor_centavos = valor_liquido_centavos) "
            "OR (natureza IN ('ANULACAO_EMPENHO', 'PAGAMENTO') "
            "AND registro_pai_id IS NOT NULL "
            "AND valor_empenhado_centavos IS NULL "
            "AND valor_anulado_centavos IS NULL "
            "AND valor_liquido_centavos IS NULL)",
            name="ck_tce_despesas_calculadas_valores_natureza",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["tce_despesa_calculo_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "registro_pai_id"],
            ["tce_despesas_calculadas.run_id", "tce_despesas_calculadas.id"],
            ondelete="RESTRICT",
            name="fk_tce_despesas_calculadas_pai_mesmo_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "natureza",
            "chave_registro",
            name="uq_tce_despesas_calculadas_run_registro",
        ),
        sa.UniqueConstraint(
            "run_id",
            "id",
            name="uq_tce_despesas_calculadas_run_id",
        ),
    )
    op.create_index(
        "idx_tce_despesas_calculadas_consulta",
        "tce_despesas_calculadas",
        ["run_id", "natureza", "competencia_analitica"],
    )
    op.create_index(
        "idx_tce_despesas_calculadas_fornecedor",
        "tce_despesas_calculadas",
        ["cnpj_fornecedor", "competencia_analitica"],
    )
    op.create_index(
        "idx_tce_despesas_calculadas_documento_fornecedor",
        "tce_despesas_calculadas",
        [
            "tipo_documento_fornecedor",
            "documento_fornecedor",
            "competencia_analitica",
        ],
    )
    op.create_index(
        "idx_tce_despesas_calculadas_orgao_natureza",
        "tce_despesas_calculadas",
        ["run_id", "codigo_orgao", "natureza_despesa_codigo"],
    )
    op.create_index(
        "idx_tce_despesas_calculadas_empenho",
        "tce_despesas_calculadas",
        ["chave_empenho"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tce_despesas_calculadas_orgao_natureza",
        table_name="tce_despesas_calculadas",
    )
    op.drop_index(
        "idx_tce_despesas_calculadas_documento_fornecedor",
        table_name="tce_despesas_calculadas",
    )
    op.drop_index(
        "idx_tce_despesas_calculadas_empenho",
        table_name="tce_despesas_calculadas",
    )
    op.drop_index(
        "idx_tce_despesas_calculadas_fornecedor",
        table_name="tce_despesas_calculadas",
    )
    op.drop_index(
        "idx_tce_despesas_calculadas_consulta",
        table_name="tce_despesas_calculadas",
    )
    op.drop_table("tce_despesas_calculadas")
    op.drop_index(
        "idx_tce_despesa_calculo_runs_consulta",
        table_name="tce_despesa_calculo_runs",
    )
    op.drop_index(
        "idx_tce_despesa_calculo_runs_escopo_historico",
        table_name="tce_despesa_calculo_runs",
    )
    op.drop_index(
        "uq_tce_despesa_calculo_run_publicado_escopo",
        table_name="tce_despesa_calculo_runs",
    )
    op.drop_table("tce_despesa_calculo_runs")
