"""Rotas de consulta e integração interna do matching NLP."""
from __future__ import annotations

import hmac
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import obter_request_id, resposta_erro
from app.api.schemas.matching import MatchingResultsResponse
from app.core import orm
from app.nlp.matching.pipeline import consultar_resultados_batch, consultar_status_batch
from app.nlp.matching.schemas import (
    BatchAccepted,
    BatchStatusResponse,
    MatchingResultsPage,
)
from app.nlp.matching.service import enfileirar_batch_http
from app.nlp.profiles.schemas import MatchingBatchRequest
from app.nlp.repositories.matching import listar_resultados_filtrados_session


router = APIRouter(prefix="/matching", tags=["Compatibilidade NLP"])
internal_router = APIRouter(prefix="/internal/matching", tags=["Matching interno"])
_bearer = HTTPBearer(auto_error=False, scheme_name="MatchingServiceBearer")


def exigir_token_matching(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    configured_token = os.getenv("MATCHING_INTERNAL_API_TOKEN", "").strip()
    if not configured_token:
        raise HTTPException(status_code=503, detail="Matching interno nao configurado.")
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"),
            configured_token.encode("utf-8"),
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Credencial de servico invalida.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@internal_router.post(
    "/batches",
    status_code=202,
    response_model=BatchAccepted,
    dependencies=[Depends(exigir_token_matching)],
    summary="Enfileira batch de matching empresarial",
    description=(
        "Valida e persiste um snapshot matching.v1 e seu job no PostgreSQL Analytics. "
        "O processamento NLP ocorre de forma assíncrona no worker dedicado."
    ),
)
def criar_batch_matching(
    payload: MatchingBatchRequest,
    request: Request,
    response: Response,
) -> BatchAccepted | Response:
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        return enfileirar_batch_http(payload)
    except ValueError:
        return resposta_erro(
            status_code=409,
            code="BATCH_ID_CONFLICT",
            message="O batch_id ja esta associado a outro payload.",
            details=[],
            request_id=request_id,
        )
    except SQLAlchemyError:
        return resposta_erro(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Nao foi possivel registrar o batch.",
            details=[],
            request_id=request_id,
        )


@internal_router.get(
    "/batches/{batch_id}",
    response_model=BatchStatusResponse,
    dependencies=[Depends(exigir_token_matching)],
    summary="Consulta estado de batch de matching",
)
def obter_status_batch_matching(
    batch_id: UUID,
    request: Request,
    response: Response,
) -> dict[str, object] | Response:
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        resultado = consultar_status_batch(str(batch_id))
        if resultado is None:
            return resposta_erro(
                status_code=404,
                code="BATCH_NOT_FOUND",
                message="Batch nao encontrado.",
                details=[],
                request_id=request_id,
            )
        return resultado
    except SQLAlchemyError:
        return resposta_erro(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Nao foi possivel consultar o batch.",
            details=[],
            request_id=request_id,
        )


@internal_router.get(
    "/batches/{batch_id}/results",
    response_model=MatchingResultsPage,
    dependencies=[Depends(exigir_token_matching)],
    summary="Consulta resultados paginados de um batch",
)
def obter_resultados_batch_matching(
    batch_id: UUID,
    request: Request,
    response: Response,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None, min_length=1, max_length=2048),
) -> dict[str, object] | Response:
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        resultado = consultar_resultados_batch(
            str(batch_id), limit=limit, cursor=cursor,
        )
        if resultado is None:
            return resposta_erro(
                status_code=404,
                code="BATCH_NOT_FOUND",
                message="Batch nao encontrado.",
                details=[],
                request_id=request_id,
            )
        return resultado
    except ValueError:
        return resposta_erro(
            status_code=400,
            code="INVALID_CURSOR",
            message="O cursor de resultados e invalido.",
            details=[],
            request_id=request_id,
        )
    except SQLAlchemyError:
        return resposta_erro(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Nao foi possivel consultar os resultados.",
            details=[],
            request_id=request_id,
        )


@router.get(
    "/results",
    response_model=MatchingResultsResponse,
    dependencies=[Depends(exigir_token_matching)],
    summary="Lista resultados de compatibilidade NLP",
    description=(
        "Consulta snapshots persistidos sem executar matching nem acessar fontes externas. "
        "Retorna apenas HIGH. Os filtros por empresa e batch são opcionais. Use `batch_id` para limitar "
        "a consulta a uma execução específica."
    ),
)
def listar_resultados_matching(
    request: Request,
    response: Response,
    company_id: UUID | None = Query(None, description="UUID da empresa do perfil analisado."),
    batch_id: UUID | None = Query(None, description="UUID do batch a consultar."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _consultar(
        request, response, company_id=company_id,
        batch_id=batch_id,
        limit=limit, offset=offset,
    )


@router.get(
    "/companies/{company_id}/results",
    response_model=MatchingResultsResponse,
    dependencies=[Depends(exigir_token_matching)],
    summary="Lista resultados de compatibilidade de uma empresa",
    description=(
        "Consulta apenas os editais HIGH para uma empresa, opcionalmente por batch."
    ),
)
def listar_resultados_empresa(
    company_id: UUID,
    request: Request,
    response: Response,
    batch_id: UUID | None = Query(None, description="UUID do batch a consultar."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _consultar(
        request, response, company_id=company_id,
        batch_id=batch_id,
        limit=limit, offset=offset,
    )


def _consultar(
    request: Request,
    response: Response,
    *,
    company_id: UUID | None,
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


__all__ = ["internal_router", "router"]
