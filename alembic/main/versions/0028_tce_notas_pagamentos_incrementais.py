"""Versioned incremental TCE payment notes by budget and document month."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0028_tce_pagamentos_incrementais"
down_revision = "0027_nlp_matching_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_pagamento_ingestion_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("competencia", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publicado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("substituido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quantidade_pagamentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("problemas", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('EM_VALIDACAO', 'PUBLICADO', 'SUBSTITUIDO', 'REJEITADO')",
            name="ck_tce_pagamento_runs_status",
        ),
        sa.CheckConstraint("quantidade_pagamentos >= 0", name="ck_tce_pagamento_runs_qtd"),
        sa.CheckConstraint(
            "exercicio_orcamento > 0 AND exercicio_orcamento % 100 = 0",
            name="ck_tce_pagamento_runs_exercicio",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_tce_pagamento_run_publicado_competencia",
        "tce_pagamento_ingestion_runs",
        ["codigo_municipio_tce", "exercicio_orcamento", "competencia"],
        unique=True,
        postgresql_where=sa.text("status = 'PUBLICADO'"),
    )
    op.create_index(
        "idx_tce_pagamento_runs_status_expiracao",
        "tce_pagamento_ingestion_runs",
        ["status", "expira_em"],
    )
    op.create_table(
        "tce_pagamentos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("chave_pagamento", sa.Text(), nullable=False),
        sa.Column("chave_empenho", sa.Text(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("codigo_unidade_orcamentaria", sa.Text(), nullable=False),
        sa.Column("data_emissao_empenho", sa.Date(), nullable=False),
        sa.Column("numero_empenho", sa.Text(), nullable=False),
        sa.Column("numero_sub_empenho", sa.Text(), nullable=False),
        sa.Column("numero_nota_pagamento", sa.Text(), nullable=False),
        sa.Column("data_nota_pagamento", sa.Date(), nullable=False),
        sa.Column("data_referencia_doc", sa.Integer(), nullable=False),
        sa.Column("estado_de_estornado", sa.Text(), nullable=False),
        sa.Column("valor_nota_pagamento_centavos", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("valor_nota_pagamento_centavos >= 0", name="ck_tce_pagamentos_valor_nao_negativo"),
        sa.UniqueConstraint("run_id", "chave_pagamento", name="uq_tce_pagamentos_run_chave"),
        sa.ForeignKeyConstraint(["run_id"], ["tce_pagamento_ingestion_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tce_pagamentos_run", "tce_pagamentos", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_tce_pagamentos_run", table_name="tce_pagamentos")
    op.drop_table("tce_pagamentos")
    op.drop_index("idx_tce_pagamento_runs_status_expiracao", table_name="tce_pagamento_ingestion_runs")
    op.drop_index("uq_tce_pagamento_run_publicado_competencia", table_name="tce_pagamento_ingestion_runs")
    op.drop_table("tce_pagamento_ingestion_runs")
