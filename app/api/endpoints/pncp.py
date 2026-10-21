"""API somente leitura das oportunidades PNCP já persistidas."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import obter_request_id, resposta_erro
from app.pncp import serving


router = APIRouter(prefix="/pncp", tags=["Oportunidades PNCP"])


def _executar(request: Request, response: Response, operacao):
    request_id = obter_request_id(request)
    response.headers["X-Request-ID"] = request_id
    try:
        return operacao()
    except LookupError as error:
        return resposta_erro(
            status_code=404,
            code="NOT_FOUND",
            message=str(error),
            details=[],
            request_id=request_id,
        )
    except SQLAlchemyError:
        return resposta_erro(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Banco indisponivel.",
            details=[],
            request_id=request_id,
        )


@router.get("/editais", summary="Busca editais PNCP normalizados localmente")
def buscar_editais(
    request: Request,
    response: Response,
    busca: str | None = Query(None, min_length=1, max_length=200),
    pagina: int = Query(1, ge=1),
    tamanho_pagina: int = Query(20, ge=1, le=100),
):
    return _executar(
        request,
        response,
        lambda: serving.listar_editais(
            busca=busca,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        ),
    )


@router.get("/editais/{external_id:path}", summary="Consulta um edital PNCP persistido")
def buscar_edital(external_id: str, request: Request, response: Response):
    return _executar(request, response, lambda: serving.obter_edital(external_id))
