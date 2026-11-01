"""Sincroniza o catálogo público CNAE-Subclasses do IBGE e pesquisa o espelho local."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

import requests
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import PncpMapaCnaeCategoria, PncpMapaRevisao, PncpTaxonomiaCnae


IBGE_CNAE_SUBCLASSES_URL = "https://servicodados.ibge.gov.br/api/v2/cnae/subclasses"
IBGE_CNAE_FONTE = "IBGE API CNAE v2 - CNAE-Subclasses 2.3"


class ErroCatalogoCnaeIbge(RuntimeError):
    """Resposta inválida ou indisponível do catálogo CNAE do IBGE."""


def normalizar_subclasses(payload: Any) -> list[dict[str, str]]:
    """Valida a lista de subclasses retornada pelo endpoint oficial do IBGE."""
    if not isinstance(payload, list) or not payload:
        raise ErroCatalogoCnaeIbge("IBGE retornou catálogo de subclasses vazio ou inválido")
    registros: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        codigo = str(item.get("id", item.get("codigo", ""))).strip()
        descricao = str(item.get("descricao", "")).strip()
        if len(codigo) == 7 and codigo.isdigit() and descricao:
            registros[codigo] = descricao
    if not registros:
        raise ErroCatalogoCnaeIbge("IBGE não retornou subclasses válidas com código e descrição")
    return [{"codigo": codigo, "descricao": descricao} for codigo, descricao in sorted(registros.items())]


def baixar_catalogo_ibge(*, timeout: tuple[int, int] = (5, 30)) -> list[dict[str, str]]:
    """Baixa todas as subclasses publicadas pelo endpoint oficial CNAE v2."""
    try:
        resposta = requests.get(IBGE_CNAE_SUBCLASSES_URL, timeout=timeout)
        resposta.raise_for_status()
        return normalizar_subclasses(resposta.json())
    except (requests.RequestException, ValueError) as erro:
        raise ErroCatalogoCnaeIbge("Não foi possível obter um catálogo CNAE válido do IBGE") from erro


def salvar_catalogo_session(session: Session, registros: list[dict[str, str]]) -> int:
    """Upsert idempotente das descrições oficiais sem tocar nas relações versionadas."""
    normalizados = normalizar_subclasses(registros)
    fonte = f"{IBGE_CNAE_FONTE}; sincronizado_em={datetime.now(timezone.utc).isoformat()}"
    for inicio in range(0, len(normalizados), 250):
        valores = [
            {"codigo": item["codigo"], "descricao": item["descricao"], "fonte": fonte}
            for item in normalizados[inicio:inicio + 250]
        ]
        comando = insert(PncpTaxonomiaCnae).values(valores)
        session.execute(comando.on_conflict_do_update(
            index_elements=[PncpTaxonomiaCnae.codigo],
            set_={"descricao": comando.excluded.descricao, "fonte": comando.excluded.fonte},
        ))
    return len(normalizados)


def buscar_catalogo_session(session: Session, *, consulta: str, limite: int = 20) -> dict[str, Any]:
    """Busca por código ou descrição no catálogo local sincronizado do IBGE."""
    consulta = consulta.strip()
    if len(consulta) < 2 or len(consulta) > 120:
        raise ValueError("A busca deve ter entre 2 e 120 caracteres")
    if not 1 <= limite <= 100:
        raise ValueError("O limite deve estar entre 1 e 100")

    padrao = f"%{consulta}%"
    codigo_normalizado = re.sub(r"\D", "", consulta)
    condicoes = [PncpTaxonomiaCnae.descricao.ilike(padrao)]
    if codigo_normalizado:
        condicoes.extend((
            PncpTaxonomiaCnae.codigo.ilike(padrao),
            PncpTaxonomiaCnae.codigo.ilike(f"%{codigo_normalizado}%"),
        ))
    stmt = select(PncpTaxonomiaCnae).where(
        or_(*condicoes)
    ).order_by(PncpTaxonomiaCnae.codigo).limit(limite)
    registros = list(session.scalars(stmt).all())

    versao = session.scalar(select(PncpMapaRevisao.versao).where(PncpMapaRevisao.estado == "ATIVA"))
    mapeados: dict[str, list[str]] = {}
    if versao and registros:
        linhas = session.execute(
            select(PncpMapaCnaeCategoria.cnae_codigo, PncpMapaCnaeCategoria.categoria_slug)
            .where(
                PncpMapaCnaeCategoria.versao == versao,
                PncpMapaCnaeCategoria.ativo.is_(True),
                PncpMapaCnaeCategoria.cnae_codigo.in_([r.codigo for r in registros]),
            )
            .order_by(PncpMapaCnaeCategoria.categoria_slug)
        ).all()
        for codigo, categoria in linhas:
            mapeados.setdefault(codigo, []).append(categoria)

    return {
        "fonte": IBGE_CNAE_FONTE,
        "versao_mapa": versao,
        "quantidade": len(registros),
        "resultados": [{
            "codigo": r.codigo, "descricao": r.descricao, "fonte_catalogo": r.fonte,
            "categorias_mapeadas": mapeados.get(r.codigo, []),
            "mapeado": bool(mapeados.get(r.codigo)),
        } for r in registros],
    }
