"""Cache persistente das respostas JSON das rotas de consulta."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0018_api_response_cache"
down_revision = "0017_pncp_qualidade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_response_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("request_method", sa.Text(), nullable=False),
        sa.Column("request_path", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_content_type", sa.Text(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column(
            "cached_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_api_response_cache_expires_at",
        "api_response_cache",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_response_cache_expires_at", table_name="api_response_cache")
    op.drop_table("api_response_cache")
