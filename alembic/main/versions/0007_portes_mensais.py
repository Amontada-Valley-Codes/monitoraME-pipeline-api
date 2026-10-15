"""Versiona retratos mensais reais de porte, sem alterar o cache existente."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_portes_mensais"
down_revision = "0006_tce_empenhos_fornecedor_idx"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("porte_publicacoes",
        sa.Column("versao", sa.Text(), primary_key=True),
        sa.Column("competencia", sa.Text(), nullable=False),
        sa.Column("ativa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("observado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("importado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("raizes_selecionadas", sa.Integer(), nullable=False),
        sa.Column("raizes_encontradas", sa.Integer(), nullable=False),
        sa.Column("manifesto", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("competencia ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_porte_competencia"),
        sa.CheckConstraint("raizes_selecionadas >= 0 AND raizes_encontradas >= 0 AND raizes_encontradas <= raizes_selecionadas", name="ck_porte_cobertura"),
    )
    op.create_index("uq_porte_competencia_ativa", "porte_publicacoes", ["competencia"], unique=True, postgresql_where=sa.text("ativa"))
    op.create_table("porte_observacoes",
        sa.Column("versao", sa.Text(), sa.ForeignKey("porte_publicacoes.versao", ondelete="RESTRICT"), primary_key=True),
        sa.Column("raiz", sa.Text(), primary_key=True),
        sa.Column("porte_codigo", sa.Text(), nullable=True),
        sa.Column("optante_mei", sa.Boolean(), nullable=True),
        sa.Column("simples", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.CheckConstraint("raiz ~ '^[A-Z0-9]{8}$'", name="ck_porte_raiz"),
        sa.CheckConstraint("porte_codigo IS NULL OR porte_codigo IN ('00','01','03','05')", name="ck_porte_codigo"),
    )
    op.create_index("idx_porte_observacoes_raiz", "porte_observacoes", ["raiz"])


def downgrade():
    op.drop_table("porte_observacoes")
    op.drop_table("porte_publicacoes")
