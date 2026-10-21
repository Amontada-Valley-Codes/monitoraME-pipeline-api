"""Cria estado global de rate limit e circuit breaker PNCP."""
from alembic import op
import sqlalchemy as sa


revision = "0013_pncp_source_control"
down_revision = "0012_pncp_job_error_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pncp_source_controls",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("circuit_state", sa.Text(), nullable=False, server_default=sa.text("'CLOSED'")),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("open_until", sa.DateTime(timezone=True)),
        sa.Column("half_open_in_flight", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("active_requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_request_at", sa.DateTime(timezone=True)),
        sa.Column("interval_milliseconds", sa.Integer(), nullable=False, server_default=sa.text("500")),
        sa.Column("successes_since_reduction", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_reason", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')", name="ck_pncp_source_control_circuit"),
        sa.CheckConstraint("concurrency_limit >= 1 AND active_requests >= 0", name="ck_pncp_source_control_concurrency"),
    )


def downgrade() -> None:
    op.drop_table("pncp_source_controls")
