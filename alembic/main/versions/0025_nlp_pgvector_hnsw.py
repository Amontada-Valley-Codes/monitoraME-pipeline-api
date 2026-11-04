"""Converte embeddings PNCP para pgvector e adiciona indice HNSW."""
from alembic import op


revision = "0025_nlp_pgvector_hnsw"
down_revision = "0024_nlp_procurement_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE nlp_procurement_embeddings "
        "ALTER COLUMN vector TYPE vector(384) "
        "USING vector::text::vector(384)"
    )
    op.execute(
        "CREATE INDEX idx_nlp_opportunities_modality_code "
        "ON pncp_oportunidades ((dados_semanticos #>> '{modalidade_codigo,valor}'))"
    )
    op.execute(
        "CREATE INDEX idx_nlp_opportunities_ibge_prefix "
        "ON pncp_oportunidades ((unidade ->> 'codigoIbge') text_pattern_ops)"
    )
    op.create_index(
        "idx_nlp_opportunities_derived_status",
        "pncp_oportunidades",
        ["derived_status"],
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_nlp_procurement_embeddings_hnsw",
            "nlp_procurement_embeddings",
            ["vector"],
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
            postgresql_with={"m": 12, "ef_construction": 48},
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.drop_index("idx_nlp_opportunities_derived_status", table_name="pncp_oportunidades")
    op.execute("DROP INDEX idx_nlp_opportunities_ibge_prefix")
    op.execute("DROP INDEX idx_nlp_opportunities_modality_code")
    op.drop_index(
        "idx_nlp_procurement_embeddings_hnsw",
        table_name="nlp_procurement_embeddings",
    )
    op.execute(
        "ALTER TABLE nlp_procurement_embeddings "
        "ALTER COLUMN vector TYPE jsonb USING to_jsonb(vector::real[])"
    )
