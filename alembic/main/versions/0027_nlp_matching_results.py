"""Persist local matching batches and versioned results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0027_nlp_matching_results"
down_revision = "0026_pncp_cnae_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nlp_matching_batches",
        sa.Column("batch_id", sa.Text(), primary_key=True),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("profiles", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("total_companies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_companies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_companies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')", name="ck_nlp_matching_batch_status"),
        sa.CheckConstraint(
            "total_companies >= 0 AND processed_companies >= 0 AND failed_companies >= 0 "
            "AND processed_companies + failed_companies <= total_companies",
            name="ck_nlp_matching_batch_counts",
        ),
    )
    op.create_table(
        "nlp_matching_results",
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("procurement_version", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("matching_version", sa.Text(), nullable=False),
        sa.Column("taxonomy_version", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("eligibility", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_nlp_matching_result_score"),
        sa.CheckConstraint("eligibility IN ('ELIGIBLE', 'INELIGIBLE', 'NOT_CONFIRMED')", name="ck_nlp_matching_result_eligibility"),
        sa.PrimaryKeyConstraint(
            "batch_id", "company_id", "source", "external_id", "profile_version",
            "procurement_version", "model_version", "matching_version", "taxonomy_version",
            name="pk_nlp_matching_results",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["nlp_matching_batches.batch_id"], ondelete="CASCADE"),
    )
    op.create_index("idx_nlp_matching_results_company", "nlp_matching_results", ["company_id", "processed_at"])


def downgrade() -> None:
    op.drop_index("idx_nlp_matching_results_company", table_name="nlp_matching_results")
    op.drop_table("nlp_matching_results")
    op.drop_table("nlp_matching_batches")
