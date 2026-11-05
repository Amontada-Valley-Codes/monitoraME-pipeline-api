"""Progress of supplier enrichment for published TCE expenses."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core import database, orm
from app.core.models import IbgeMunicipio, TceEnriquecimentoMunicipio, TceMunicipio


def listar_municipios_enriquecimento(uf: str) -> list[str]:
    database.init_db()
    with orm.main_session() as session:
        return list(session.scalars(
            select(TceMunicipio.codigo_municipio_tce)
            .join(IbgeMunicipio, IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge)
            .where(IbgeMunicipio.uf == uf.upper())
            .order_by(TceMunicipio.codigo_municipio_tce)
        ).all())


def municipios_concluidos(ano: int) -> set[str]:
    database.init_db()
    with orm.main_session() as session:
        return set(session.scalars(
            select(TceEnriquecimentoMunicipio.codigo_municipio_tce)
            .where(TceEnriquecimentoMunicipio.ano == ano)
        ).all())


def salvar_municipio_concluido(ano: int, codigo: str, total: int, com_porte: int) -> None:
    """Commit only after all supplier batches for the municipality finished."""
    database.init_db()
    tabela = TceEnriquecimentoMunicipio.__table__
    with orm.main_session() as session:
        session.execute(insert(tabela).values(
            ano=ano, codigo_municipio_tce=codigo,
            total_cnpjs=total, cnpjs_com_porte=com_porte,
        ).on_conflict_do_nothing(index_elements=["ano", "codigo_municipio_tce"]))
