"""Cursor independente e visao atual versionada de contratacoes PNCP."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_pncp_atualizacao"
down_revision = "0008_pncp_raw_records"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("pncp_cursors", sa.Column("flow", sa.Text(), primary_key=True), sa.Column("scope_key", sa.Text(), primary_key=True), sa.Column("last_confirmed_end", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_table("pncp_contratacoes_atuais", sa.Column("source", sa.Text(), primary_key=True), sa.Column("external_id", sa.Text(), primary_key=True), sa.Column("payload_hash", sa.Text(), nullable=False), sa.Column("source_updated_at", sa.DateTime(timezone=True)), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_index("idx_pncp_contratacoes_atuais_atualizacao", "pncp_contratacoes_atuais", ["source_updated_at"])

def downgrade():
    op.drop_table("pncp_contratacoes_atuais")
    op.drop_table("pncp_cursors")
