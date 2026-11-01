"""Taxonomia e mapa CNAE-categoria versionado para matching PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0026_pncp_cnae_taxonomy"
down_revision = "0025_nlp_pgvector_hnsw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pncp_taxonomia_categorias",
        sa.Column("slug", sa.Text(), primary_key=True),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
    )
    op.create_table(
        "pncp_taxonomia_cnaes",
        sa.Column("codigo", sa.Text(), primary_key=True),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
    )
    op.create_table(
        "pncp_mapa_revisoes",
        sa.Column("versao", sa.Text(), primary_key=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default="RASCUNHO"),
        sa.Column("evidencia", sa.Text(), nullable=False),
        sa.Column("criado_por", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ativado_por", sa.Text()),
        sa.Column("ativado_em", sa.DateTime(timezone=True)),
        sa.CheckConstraint("estado IN ('RASCUNHO', 'ATIVA', 'SUBSTITUIDA')", name="ck_pncp_mapa_estado"),
    )
    op.create_index("uq_pncp_mapa_versao_ativa", "pncp_mapa_revisoes", ["estado"], unique=True, postgresql_where=sa.text("estado = 'ATIVA'"))
    op.create_table(
        "pncp_mapa_cnae_categoria",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("versao", sa.Text(), sa.ForeignKey("pncp_mapa_revisoes.versao", ondelete="RESTRICT"), nullable=False),
        sa.Column("cnae_codigo", sa.Text(), sa.ForeignKey("pncp_taxonomia_cnaes.codigo", ondelete="RESTRICT"), nullable=False),
        sa.Column("categoria_slug", sa.Text(), sa.ForeignKey("pncp_taxonomia_categorias.slug", ondelete="RESTRICT"), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("confianca", sa.Float(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("condicao", sa.Text()),
        sa.Column("evidencias", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("revisado_por", sa.Text(), nullable=False),
        sa.Column("revisado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("versao", "cnae_codigo", "categoria_slug", name="uq_pncp_mapa_relacao"),
        sa.CheckConstraint("tipo IN ('DIRETA', 'RELACIONADA', 'CONDICIONAL')", name="ck_pncp_mapa_tipo"),
        sa.CheckConstraint("confianca >= 0 AND confianca <= 1", name="ck_pncp_mapa_confianca"),
        sa.CheckConstraint("tipo != 'CONDICIONAL' OR condicao IS NOT NULL", name="ck_pncp_mapa_condicao"),
    )
    op.create_index("idx_pncp_mapa_snapshot", "pncp_mapa_cnae_categoria", ["versao", "ativo", "cnae_codigo"])
    op.execute("""
        INSERT INTO pncp_taxonomia_categorias (slug, nome, descricao) VALUES
        ('software', 'Software e serviços de TI', 'Desenvolvimento, licenciamento e consultoria em tecnologia da informação.'),
        ('energia-eletrica', 'Energia elétrica', 'Distribuição e serviços diretamente relacionados à energia elétrica.'),
        ('consultoria-gestao', 'Consultoria em gestão', 'Consultoria empresarial e serviços de gestão não técnica.'),
        ('alimentacao', 'Alimentação e refeições', 'Fornecimento de refeições e serviços de alimentação preparada.')
    """)
    op.execute("""
        INSERT INTO pncp_taxonomia_cnaes (codigo, descricao, fonte) VALUES
        ('6201501', 'Desenvolvimento de programas de computador sob encomenda', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('6203100', 'Desenvolvimento e licenciamento de programas de computador não-customizáveis', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('6204000', 'Consultoria em tecnologia da informação', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('3514000', 'Distribuição de energia elétrica', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('7020400', 'Atividades de consultoria em gestão empresarial, exceto consultoria técnica específica', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('5620101', 'Fornecimento de alimentos preparados preponderantemente para empresas', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('5620102', 'Serviços de alimentação para eventos e recepções - bufê', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json'),
        ('5620104', 'Fornecimento de alimentos preparados preponderantemente para consumo domiciliar', 'CNAE-Subclasses 2.3 IBGE/CONCLA; tests/nlp/fixtures/companies.json')
    """)
    op.execute("""
        INSERT INTO pncp_mapa_revisoes (versao, estado, evidencia, criado_por, ativado_por, ativado_em)
        VALUES ('cnae-objeto-v1', 'ATIVA',
          'Curadoria inicial por correspondência semântica entre descrições CNAE-Subclasses 2.3 IBGE/CONCLA no fixture local e taxonomia PNCP; mapeamentos candidatos, não prova de habilitação legal.',
          'migration-0026-curadoria-inicial', 'migration-0026-curadoria-inicial', now())
    """)
    op.execute("""
        INSERT INTO pncp_mapa_cnae_categoria (versao, cnae_codigo, categoria_slug, tipo, confianca, evidencias, revisado_por) VALUES
        ('cnae-objeto-v1','6201501','software','DIRETA',0.95,'["Descrição CNAE: desenvolvimento de programas sob encomenda"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','6203100','software','DIRETA',0.95,'["Descrição CNAE: desenvolvimento e licenciamento de software"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','6204000','software','DIRETA',0.90,'["Descrição CNAE: consultoria em tecnologia da informação"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','3514000','energia-eletrica','DIRETA',0.98,'["Descrição CNAE: distribuição de energia elétrica"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','7020400','consultoria-gestao','DIRETA',0.95,'["Descrição CNAE: consultoria em gestão empresarial"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','5620101','alimentacao','DIRETA',0.95,'["Descrição CNAE: fornecimento de alimentos preparados para empresas"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','5620102','alimentacao','DIRETA',0.95,'["Descrição CNAE: serviços de alimentação para eventos"]','curadoria-inicial-v1'),
        ('cnae-objeto-v1','5620104','alimentacao','DIRETA',0.95,'["Descrição CNAE: fornecimento de alimentos preparados para consumo domiciliar"]','curadoria-inicial-v1')
    """)


def downgrade() -> None:
    op.drop_index("idx_pncp_mapa_snapshot", table_name="pncp_mapa_cnae_categoria")
    op.drop_table("pncp_mapa_cnae_categoria")
    op.drop_index("uq_pncp_mapa_versao_ativa", table_name="pncp_mapa_revisoes")
    op.drop_table("pncp_mapa_revisoes")
    op.drop_table("pncp_taxonomia_cnaes")
    op.drop_table("pncp_taxonomia_categorias")
