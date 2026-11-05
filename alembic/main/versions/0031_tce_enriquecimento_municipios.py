"""Track completed TCE supplier enrichment by year and municipality."""

from alembic import op
import sqlalchemy as sa

revision = "0031_tce_enriquecimento_municipios"
down_revision = "0030_api_response_cache_sem_ttl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tce_enriquecimento_municipios",
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("codigo_municipio_tce", sa.Text(), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("total_cnpjs", sa.Integer(), nullable=False),
        sa.Column("cnpjs_com_porte", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("ano", "codigo_municipio_tce"),
    )


def downgrade() -> None:
    op.drop_table("tce_enriquecimento_municipios")
