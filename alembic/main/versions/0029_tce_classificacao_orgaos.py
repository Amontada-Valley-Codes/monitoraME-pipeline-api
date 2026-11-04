"""Persist municipal executive organ classification for TCE analytics."""

from alembic import op
import sqlalchemy as sa


revision = "0029_tce_classificacao_orgaos"
down_revision = "0028_tce_pagamentos_incrementais"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_orgaos_classificacao",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("exercicio_orcamento", sa.Integer(), nullable=False),
        sa.Column("codigo_orgao", sa.Text(), nullable=False),
        sa.Column("nome_orgao", sa.Text(), nullable=False),
        sa.Column("codigo_tipo_unidade", sa.Text(), nullable=True),
        sa.Column("classificacao", sa.Text(), nullable=False),
        sa.Column("regra", sa.Text(), nullable=False),
        sa.Column("confianca", sa.Text(), nullable=False),
        sa.Column("fonte", sa.Text(), server_default=sa.text("'TCE-CE /orgaos'"), nullable=False),
        sa.Column("observado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "codigo_municipio_tce", "exercicio_orcamento", "codigo_orgao",
            name="uq_tce_orgao_classificacao_escopo",
        ),
    )
    op.create_index(
        "idx_tce_orgaos_classificacao_consulta",
        "tce_orgaos_classificacao",
        ["codigo_municipio_tce", "exercicio_orcamento", "classificacao"],
    )


def downgrade() -> None:
    op.drop_index("idx_tce_orgaos_classificacao_consulta", table_name="tce_orgaos_classificacao")
    op.drop_table("tce_orgaos_classificacao")
