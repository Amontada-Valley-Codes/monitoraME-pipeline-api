"""Rotas somente leitura para inspecionar resultados de compatibilidade NLP."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import obter_request_id, resposta_erro
from app.api.schemas.matching import MatchingResultsResponse
from app.core import orm
from app.nlp.matching.schemas import CompatibilityLevel
from app.nlp.repositories.matching import listar_resultados_filtrados_session


router = APIRouter(prefix="/matching", tags=["Compatibilidade NLP"])


@router.get(
    "/results",
    response_model=MatchingResultsResponse,
    summary="Lista resultados de compatibilidade NLP",
    description=(
        "Consulta snapshots persistidos sem executar matching nem acessar fontes externas. "
        "Os filtros por empresa, nível e batch são opcionais. Use `batch_id` para limitar "
        "a consulta a uma execução específica."
    ),
)
def listar_resultados_matching(
    request: Request,
    response: Response,
    company_id: UUID | None = Query(None, description="UUID da empresa do perfil analisado."),
    compatibility_level: CompatibilityLevel | None = Query(
        None, description="Nível de compatibilidade: LOW, MEDIUM ou HIGH."
    ),
    batch_id: UUID | None = Query(None, description="UUID do batch a consultar."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _consultar(
        request, response, company_id=company_id,
        compatibility_level=compatibility_level, batch_id=batch_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/companies/{company_id}/results",
    response_model=MatchingResultsResponse,
    summary="Lista resultados de compatibilidade de uma empresa",
    description=(
        "Consulta os editais avaliados para uma empresa. Permite filtrar pelo nível "
        "LOW, MEDIUM ou HIGH e, opcionalmente, por batch."
    ),
)
def listar_resultados_empresa(
    company_id: UUID,
    request: Request,
    response: Response,
    compatibility_level: CompatibilityLevel | None = Query(
        None, description="Nível de compatibilidade: LOW, MEDIUM ou HIGH."
    ),
    batch_id: UUID | None = Query(None, description="UUID do batch a consultar."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _consultar(
        request, response, company_id=company_id,
        compatibility_level=compatibility_level, batch_id=batch_id,
        limit=limit, offset=offset,
    )


def _consultar(
    request: Request,
    response: Response,
    *,
    company_id: UUID | None,
    compatibility_level: CompatibilityLevel | None,
    batch_id: UUID | None,
    limit: int,
    offset: int,
) -> dict[str, object] | Response:
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        with orm.main_session() as session:
            results, total = listar_resultados_filtrados_session(
                session,
                company_id=str(company_id) if company_id else None,
                compatibility_level=compatibility_level.value if compatibility_level else None,
                batch_id=str(batch_id) if batch_id else None,
                limit=limit,
                offset=offset,
            )
        return {"total": total, "limit": limit, "offset": offset, "results": results}
    except SQLAlchemyError:
        return resposta_erro(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Banco indisponivel.",
            details=[],
            request_id=request_id,
        )
