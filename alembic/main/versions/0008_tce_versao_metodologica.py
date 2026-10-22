"""add methodology version to TCE publications

Revision ID: 0008_tce_methodology
Revises: 0007_portes_mensais
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_tce_methodology"
down_revision = "0007_portes_mensais"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tce_despesa_ingestion_runs",
        sa.Column(
            "versao_metodologica",
            sa.Text(),
            server_default=sa.text("'tce-empenhos-v1'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tce_despesa_ingestion_runs", "versao_metodologica")
