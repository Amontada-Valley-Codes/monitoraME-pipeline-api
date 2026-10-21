"""Eventos idempotentes e resultados de matching PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0016_pncp_oportunidades_matching"
down_revision = "0015_pncp_enriquecimento"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("pncp_eventos", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("event_key", sa.Text(), nullable=False), sa.Column("event_type", sa.Text(), nullable=False), sa.Column("source", sa.Text(), nullable=False), sa.Column("external_id", sa.Text(), nullable=False), sa.Column("payload_hash", sa.Text(), nullable=False), sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("event_key", name="uq_pncp_eventos_event_key"))
    op.create_index("idx_pncp_eventos_contratacao", "pncp_eventos", ["source", "external_id"])
    op.create_table("pncp_matchings", sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("source", sa.Text(), nullable=False), sa.Column("external_id", sa.Text(), nullable=False), sa.Column("profile_key", sa.Text(), nullable=False), sa.Column("rule_version", sa.Text(), nullable=False), sa.Column("compatible", sa.Boolean(), nullable=False), sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("missing_fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("source", "external_id", "profile_key", "rule_version", name="uq_pncp_matching_rule"))
    op.create_index("idx_pncp_matchings_profile", "pncp_matchings", ["profile_key", "compatible"])
def downgrade() -> None:
    op.drop_table("pncp_matchings"); op.drop_table("pncp_eventos")
