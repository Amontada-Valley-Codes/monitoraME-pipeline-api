"""Checkpoints completos de sincronizacao PNCP."""
from alembic import op
import sqlalchemy as sa
revision = "0011_pncp_sync_checkpoints"
down_revision = "0010_pncp_normalizacao"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("pncp_sync_cursors", sa.Column("source", sa.Text(), primary_key=True), sa.Column("entity_type", sa.Text(), primary_key=True), sa.Column("flow", sa.Text(), primary_key=True), sa.Column("scope_key", sa.Text(), primary_key=True), sa.Column("last_confirmed_end", sa.DateTime(timezone=True)), sa.Column("last_run_id", sa.Text()), sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'IDLE'")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
def downgrade(): op.drop_table("pncp_sync_cursors")
