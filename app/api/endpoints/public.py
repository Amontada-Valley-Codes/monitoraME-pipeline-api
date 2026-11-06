"""Endpoints publicos e enxutos para paginas de apresentacao."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query

from app.core import database
from app.pipeline import analisys


router = APIRouter(prefix="/public", tags=["Dados públicos"])


@router.get(
    "/municipios/{codigo_tce}/previa",
    summary="Prévia municipal para a landing page",
    description=(
        "Retorna um resumo municipal do TCE-CE a partir de dados publicados no banco local. "
        "Não consulta fontes externas nem inicia ingestão. O período deve ser informado."
    ),
)
def obter_previa_municipal(
    codigo_tce: Annotated[str, Path(pattern=r"^[0-9]{3}$", description="Código do município no TCE-CE, com três dígitos.")],
    data_inicial: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    data_final: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
) -> dict[str, Any]:
    try:
        inicio = date.fromisoformat(data_inicial)
        fim = date.fromisoformat(data_final)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="As datas devem ser válidas no formato YYYY-MM-DD.") from error
    if inicio > fim:
        raise HTTPException(status_code=422, detail="data_inicial deve ser menor ou igual a data_final.")

    municipio = database.localizar_municipio_tce(codigo_tce)
    if municipio is None:
        raise HTTPException(status_code=404, detail="Município do Ceará não encontrado no catálogo local.")

    try:
        overview = analisys.consultar_overview_municipal_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=municipio["codigo_municipio_tce"],
            incluir_resumo_compras=True,
        )
    except analisys.CompetenciasTceIndisponiveisError as error:
        raise HTTPException(status_code=503, detail="Dados publicados indisponíveis para o período solicitado.") from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return {
        "municipio": {
            "codigo_ibge": municipio["codigo_municipio_ibge"],
            "codigo_tce": municipio["codigo_municipio_tce"],
            "nome": municipio["nome"],
            "uf": municipio["uf"],
        },
        "periodo": {"data_inicial": data_inicial, "data_final": data_final},
        "fonte": "TCE-CE",
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "data_available": bool(overview.get("data_available")),
        "is_stale": bool(overview.get("is_stale", False)),
        "competencias_publicadas": overview.get("competencias_publicadas", []),
        "kpis": overview.get("kpis", {}),
        "participacao_por_porte_empresarial": overview.get("participacao_por_porte_empresarial", []),
        "resumo_compras": overview.get("resumo_compras", {}),
    }
