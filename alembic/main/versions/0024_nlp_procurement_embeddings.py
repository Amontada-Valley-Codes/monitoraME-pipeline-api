"""Persiste embeddings de segmentos do corpus PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024_nlp_procurement_embeddings"
down_revision = "0023_nlp_procurement_segments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_procurement_embeddings",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("segment_type", sa.Text(), primary_key=True),
        sa.Column("segment_key", sa.Text(), primary_key=True),
        sa.Column("text_hash", sa.Text(), primary_key=True),
        sa.Column("model_version", sa.Text(), primary_key=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["source", "external_id", "segment_type", "segment_key"],
            ["nlp_procurement_segments.source", "nlp_procurement_segments.external_id",
             "nlp_procurement_segments.segment_type", "nlp_procurement_segments.segment_key"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_nlp_procurement_embeddings_model", "nlp_procurement_embeddings", ["model_version"])


def downgrade() -> None:
    op.drop_index("idx_nlp_procurement_embeddings_model", table_name="nlp_procurement_embeddings")
    op.drop_table("nlp_procurement_embeddings")
