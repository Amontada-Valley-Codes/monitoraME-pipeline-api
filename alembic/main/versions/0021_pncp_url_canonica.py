"""Corrige URLs armazenadas para a rota canônica de editais PNCP."""

from alembic import op

revision = "0021_pncp_url_canonica"
down_revision = "0020_pncp_recursos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        WITH identificadores AS (
            SELECT source, external_id,
                   regexp_match(external_id, '^([A-Za-z0-9]{14})-[0-9]+-([0-9]+)/([0-9]{4})$') AS partes
            FROM pncp_oportunidades
            WHERE source = 'PNCP'
        )
        UPDATE pncp_oportunidades AS oportunidade
        SET url_oficial = 'https://pncp.gov.br/app/editais/' || identificadores.partes[1] || '/' ||
            identificadores.partes[3] || '/' || lpad(identificadores.partes[2], 6, '0')
        FROM identificadores
        WHERE oportunidade.source = identificadores.source
          AND oportunidade.external_id = identificadores.external_id
          AND identificadores.partes IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE pncp_oportunidades
        SET url_oficial = 'https://pncp.gov.br/app/editais/' || external_id
        WHERE source = 'PNCP'
          AND external_id ~ '^[A-Za-z0-9]{14}-[0-9]+-[0-9]+/[0-9]{4}$'
    """)
