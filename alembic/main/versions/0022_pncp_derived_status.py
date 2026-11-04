"""Adiciona situacao operacional PNCP versionada."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0022_pncp_derived_status"
down_revision = "0021_pncp_url_canonica"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pncp_oportunidades", sa.Column("official_status", sa.Text()))
    op.add_column("pncp_oportunidades", sa.Column("derived_status", sa.Text(), server_default="INDETERMINADO", nullable=False))
    op.add_column("pncp_oportunidades", sa.Column("reason_codes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("pncp_oportunidades", sa.Column("data_confidence", sa.Text(), server_default="BAIXA", nullable=False))
    op.add_column("pncp_oportunidades", sa.Column("evaluated_at", sa.DateTime(timezone=True)))
    op.add_column("pncp_oportunidades", sa.Column("status_rule_version", sa.Text(), server_default="pncp-status-v1", nullable=False))
    op.create_index("idx_pncp_oportunidades_derived_status", "pncp_oportunidades", ["derived_status", "prazo_proposta_final"])


def downgrade() -> None:
    op.drop_index("idx_pncp_oportunidades_derived_status", table_name="pncp_oportunidades")
    for coluna in ("status_rule_version", "evaluated_at", "data_confidence", "reason_codes", "derived_status", "official_status"):
        op.drop_column("pncp_oportunidades", coluna)
