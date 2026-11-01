"""Armazena segmentos textuais preparados do corpus NLP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023_nlp_procurement_segments"
down_revision = "0022_pncp_derived_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_procurement_segments",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("external_id", sa.Text(), primary_key=True),
        sa.Column("segment_type", sa.Text(), primary_key=True),
        sa.Column("segment_key", sa.Text(), primary_key=True),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("source_version_hash", sa.Text(), nullable=False),
        sa.Column("segment_version_hash", sa.Text()),
        sa.Column("text_hash", sa.Text()),
        sa.Column("normalized_text", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Integer()),
        sa.Column("lot_id", sa.Text()),
        sa.Column("item_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("structured_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["source", "external_id"],
            ["pncp_oportunidades.source", "pncp_oportunidades.external_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_nlp_procurement_segments_opportunity",
        "nlp_procurement_segments",
        ["source", "external_id"],
    )
    op.create_index(
        "idx_nlp_procurement_segments_text_hash",
        "nlp_procurement_segments",
        ["text_hash"],
    )


def downgrade() -> None:
    op.drop_index("idx_nlp_procurement_segments_text_hash", table_name="nlp_procurement_segments")
    op.drop_index("idx_nlp_procurement_segments_opportunity", table_name="nlp_procurement_segments")
    op.drop_table("nlp_procurement_segments")
