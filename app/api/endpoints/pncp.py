"""API somente leitura das oportunidades PNCP já persistidas."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import obter_request_id, resposta_erro
from app.pncp import serving
from app.pncp import taxonomy
from app.pncp import cnae_ibge
from app.core import orm


router = APIRouter(prefix="/pncp", tags=["Oportunidades PNCP"])


@router.get("/taxonomia/cnae-categoria", summary="Snapshot versionado do de-para CNAE e categoria PNCP")
def buscar_taxonomia_cnae_categoria(request: Request, response: Response, versao: str | None = Query(None, min_length=1, max_length=100)):
    def operacao():
        with orm.main_session() as session:
            return taxonomy.obter_snapshot(session, versao)
    return _executar(request, response, operacao)


@router.get("/taxonomia/cnaes/busca", summary="Busca CNAE-Subclasses no catálogo IBGE sincronizado")
def buscar_cnaes_ibge(
    request: Request, response: Response,
    q: str = Query(..., min_length=2, max_length=120),
    limite: int = Query(20, ge=1, le=100),
):
    def operacao():
        with orm.main_session() as session:
            return cnae_ibge.buscar_catalogo_session(session, consulta=q, limite=limite)
    return _executar(request, response, operacao)


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


@router.get(
    "/editais",
    summary="Lista oportunidades PNCP normalizadas",
    description=(
        "Pesquisa e pagina oportunidades já normalizadas no banco local. "
        "Esta consulta não acessa o PNCP nem inicia sincronizações."
    ),
    response_description=(
        "Objeto com `dados`, cujos itens trazem id, órgão, unidade, objeto, modalidade, "
        "situação, prazo, URL oficial e disponibilidade de itens/anexos; `paginacao` informa página, tamanho e total."
    ),
)
def buscar_editais(
    request: Request,
    response: Response,
    busca: str | None = Query(None, min_length=1, max_length=200),
    pagina: int = Query(1, ge=1),
    tamanho_pagina: int = Query(20, ge=1, le=100),
    ano_publicacao: int | None = Query(None, ge=2000, le=2100),
    ano_compra: int | None = Query(None, ge=2000, le=2100),
):
    return _executar(
        request,
        response,
        lambda: serving.listar_editais(
            busca=busca,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
            ano_publicacao=ano_publicacao,
            ano_compra=ano_compra,
        ),
    )


@router.get("/cobertura/{ano}", summary="Estado do backfill anual de publicações PNCP")
def buscar_cobertura(ano: int, request: Request, response: Response):
    if ano < 2000 or ano > 2100:
        return resposta_erro(status_code=422, code="VALIDATION_ERROR", message="Ano deve estar entre 2000 e 2100.", details=[], request_id=obter_request_id(request))
    return _executar(request, response, lambda: serving.obter_cobertura_anual(ano))


@router.get("/editais/{external_id:path}/itens", summary="Lista itens e resultados de um edital PNCP")
def buscar_itens_edital(external_id: str, request: Request, response: Response):
    return _executar(request, response, lambda: serving.obter_itens_edital(external_id))


@router.get("/editais/{external_id:path}/anexos", summary="Lista anexos de um edital PNCP")
def buscar_anexos_edital(external_id: str, request: Request, response: Response):
    return _executar(request, response, lambda: serving.obter_anexos_edital(external_id))


@router.get("/editais/{external_id:path}/historico", summary="Lista histórico de um edital PNCP")
def buscar_historico_edital(external_id: str, request: Request, response: Response):
    return _executar(request, response, lambda: serving.obter_historico_edital(external_id))


@router.get(
    "/editais/{external_id:path}",
    summary="Detalha uma oportunidade PNCP normalizada",
    description=(
        "Retorna uma oportunidade pelo identificador externo, a partir da visão "
        "normalizada local; não consulta o PNCP."
    ),
    response_description=(
        "Objeto com dados principais e detalhe adicional quando existente. Itens, anexos e "
        "histórico são retornados separadamente nas subrotas /itens, /anexos e /historico."

    ),
)
def buscar_edital(external_id: str, request: Request, response: Response):
    return _executar(request, response, lambda: serving.obter_edital(external_id))
