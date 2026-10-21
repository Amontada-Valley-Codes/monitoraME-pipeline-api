"""Consultas locais de oportunidades PNCP para consumidores da plataforma."""
from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_, select

from app.core import orm
from app.core.models import PncpEnriquecimento, PncpOportunidade
from app.pncp.opportunities import contrato_consumo


def listar_editais(
    *,
    busca: str | None = None,
    pagina: int = 1,
    tamanho_pagina: int = 20,
) -> dict[str, Any]:
    """Lista dados normalizados locais; esta leitura nunca consulta o PNCP."""
    filtros = [PncpOportunidade.source == "PNCP"]
    termo = (busca or "").strip()
    if termo:
        padrao = f"%{termo}%"
        filtros.append(
            or_(
                PncpOportunidade.external_id.ilike(padrao),
                PncpOportunidade.objeto.ilike(padrao),
            )
        )

    with orm.main_session() as session:
        total = session.scalar(
            select(func.count())
            .select_from(PncpOportunidade)
            .where(*filtros)
        ) or 0
        consulta = (
            select(PncpOportunidade, PncpEnriquecimento)
            .outerjoin(
                PncpEnriquecimento,
                and_(
                    PncpEnriquecimento.source == PncpOportunidade.source,
                    PncpEnriquecimento.external_id == PncpOportunidade.external_id,
                ),
            )
            .where(*filtros)
            .order_by(
                PncpOportunidade.prazo_proposta_final.asc().nulls_last(),
                PncpOportunidade.normalized_at.desc(),
            )
            .offset((pagina - 1) * tamanho_pagina)
            .limit(tamanho_pagina)
        )
        registros = session.execute(consulta).all()

    return {
        "dados": [contrato_consumo(oportunidade, enriquecimento) for oportunidade, enriquecimento in registros],
        "paginacao": {
            "pagina": pagina,
            "tamanho_pagina": tamanho_pagina,
            "total": total,
        },
    }


def obter_edital(external_id: str) -> dict[str, Any]:
    """Retorna um edital e seus detalhes locais, se já enriquecidos."""
    with orm.main_session() as session:
        oportunidade = session.get(
            PncpOportunidade,
            {"source": "PNCP", "external_id": external_id},
        )
        if oportunidade is None:
            raise LookupError("Edital PNCP nao encontrado.")
        enriquecimento = session.get(
            PncpEnriquecimento,
            {"source": "PNCP", "external_id": external_id},
        )

    resultado = contrato_consumo(oportunidade, enriquecimento)
    resultado["detalhe"] = enriquecimento.detail_payload if enriquecimento else None
    resultado["itens"] = enriquecimento.items_payload if enriquecimento else None
    resultado["anexos"] = enriquecimento.documents_payload if enriquecimento else None
    return resultado
