"""Campos anuais para oportunidades PNCP."""
from alembic import op
import sqlalchemy as sa

revision = "0017_pncp_ano_publicacao"
down_revision = "0016_pncp_oportunidades_matching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pncp_oportunidades", sa.Column("data_publicacao", sa.Date(), nullable=True))
    op.add_column("pncp_oportunidades", sa.Column("ano_compra", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE pncp_oportunidades AS oportunidade
        SET data_publicacao = CASE
                WHEN raw.payload->>'dataPublicacaoPncp' ~ '^\\d{4}-\\d{2}-\\d{2}'
                THEN left(raw.payload->>'dataPublicacaoPncp', 10)::date
                ELSE NULL
            END,
            ano_compra = CASE
                WHEN raw.payload->>'anoCompra' ~ '^\\d{4}$'
                THEN (raw.payload->>'anoCompra')::integer
                ELSE NULL
            END
        FROM raw_pncp_records AS raw
        WHERE raw.id = oportunidade.raw_record_id
    """)
    op.create_index("idx_pncp_oportunidades_data_publicacao", "pncp_oportunidades", ["data_publicacao"])
    op.create_index("idx_pncp_oportunidades_ano_compra", "pncp_oportunidades", ["ano_compra"])


def downgrade() -> None:
    op.drop_index("idx_pncp_oportunidades_ano_compra", table_name="pncp_oportunidades")
    op.drop_index("idx_pncp_oportunidades_data_publicacao", table_name="pncp_oportunidades")
    op.drop_column("pncp_oportunidades", "ano_compra")
    op.drop_column("pncp_oportunidades", "data_publicacao")
