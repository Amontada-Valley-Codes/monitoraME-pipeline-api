"""Keep API response cache entries until an explicit refresh."""

from alembic import op
import sqlalchemy as sa


revision = "0030_api_response_cache_sem_ttl"
down_revision = "0029_tce_classificacao_orgaos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_api_response_cache_expires_at", table_name="api_response_cache")
    op.alter_column(
        "api_response_cache",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.execute("UPDATE api_response_cache SET expires_at = NULL")


def downgrade() -> None:
    op.execute(
        "UPDATE api_response_cache "
        "SET expires_at = now() + interval '1 hour' "
        "WHERE expires_at IS NULL"
    )
    op.alter_column(
        "api_response_cache",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.create_index(
        "ix_api_response_cache_expires_at",
        "api_response_cache",
        ["expires_at"],
        unique=False,
    )
