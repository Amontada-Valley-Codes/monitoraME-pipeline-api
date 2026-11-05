from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from app.api.errors import obter_request_id, resposta_erro_validacao, usa_erro_padronizado
from app.api import response_cache
from app.core import database, log_database, orm
from app.core.logging import (
    configurar_logging,
    desabilitar_persistencia_logs,
    habilitar_persistencia_logs,
)
from app.api.endpoints.health import health
from app.api.endpoints.pipeline import (
    tce_analitico_indicadores,
    tce_analise_territorial,
    tce_comparisons,
    tce_contratos,
    tce_empenho_detalhe,
    tce_empenhos,
    tce_fornecedor_detalhe,
    tce_fornecedor_empenhos,
    tce_fornecedores,
    tce_kpi_portes_por_mes,
    tce_overview,
    tce_overview_municipal,
)
from app.api.router import router
from app.pipeline import analisys


configurar_logging()
logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {
        "name": "Dados públicos",
        "description": "Resumos públicos para páginas de apresentação calculados sobre dados locais publicados.",
    },
    {
        "name": "Sistema",
        "description": "Verificações de disponibilidade da API e de seus componentes locais.",
    },
    {
        "name": "Oportunidades PNCP",
        "description": "Consulta somente leitura às oportunidades normalizadas e persistidas localmente.",
    },
    {
        "name": "Compatibilidade NLP",
        "description": "Consulta autenticada de resultados persistidos de compatibilidade entre empresas e editais.",
    },
    {
        "name": "Matching interno",
        "description": "Submissão autenticada de batches de matching e consulta de status/resultados para serviços internos.",
    },
    {
        "name": "Contratos TCE-CE",
        "description": "Consulta e processamento de contratos publicados pelo TCE-CE.",
    },
    {
        "name": "Indicadores TCE-CE",
        "description": "KPIs, séries e visões consolidadas de empenhos líquidos.",
    },
    {
        "name": "Comparações TCE-CE",
        "description": "Comparação de recortes históricos já armazenados no banco local.",
    },
    {
        "name": "Despesas TCE-CE",
        "description": "Listagem e detalhe de despesas publicadas, retornando somente o valor final.",
    },
    {
        "name": "Fornecedores TCE-CE",
        "description": "Consultas locais sobre fornecedores e seus empenhos publicados.",
    },
    {
        "name": "Análise territorial TCE-CE",
        "description": "Distribuição geográfica, retenção local, concentração e rankings de despesas.",
    },
    {
        "name": "Pagamentos TCE-CE",
        "description": "Apuração de pagamentos municipais e subtotal de microempresas.",
    },
    {
        "name": "Porte empresarial histórico",
        "description": "Fotografias cadastrais mensais e histórico de porte por fornecedor.",
    },
]

__all__ = [
    "analisys",
    "app",
    "health",
    "lifespan",
    "tce_analitico_indicadores",
    "tce_analise_territorial",
    "tce_comparisons",
    "tce_contratos",
    "tce_empenho_detalhe",
    "tce_empenhos",
    "tce_fornecedor_detalhe",
    "tce_fornecedor_empenhos",
    "tce_fornecedores",
    "tce_kpi_portes_por_mes",
    "tce_overview",
    "tce_overview_municipal",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    persistencia_logs_habilitada = False
    try:
        database.init_db()
        log_database.init_log_db()
        habilitar_persistencia_logs()
        persistencia_logs_habilitada = True
        yield
    except Exception as error:
        logger.critical(
            "Falha no ciclo de vida da API",
            extra={
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        raise
    finally:
        if persistencia_logs_habilitada:
            desabilitar_persistencia_logs()
        database.close_pool()
        log_database.close_log_pool()
        logger.info("Recursos da API encerrados")


app = FastAPI(
    title="Monitoramento de Contas Publicas",
    description=(
        "API de dados públicos do MonitoraME. As operações estão agrupadas pelo "
        "recurso retornado e pela finalidade da consulta. Rotas de consulta local "
        "não acionam fontes externas."
    ),
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    if (
        usa_erro_padronizado(request.url.path)
        or request.url.path.startswith("/internal/matching/")
    ):
        return resposta_erro_validacao(request, error)
    return await request_validation_exception_handler(request, error)


@app.middleware("http")
async def registrar_requisicao_http(request: Request, call_next):
    inicio = perf_counter()
    corpo_cache = b""
    cache_ativo = response_cache.rota_tem_cache(request)
    resposta_do_cache = False
    medir_banco = request.headers.get("x-load-test", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    token_medicao_banco = orm.iniciar_medicao_banco() if medir_banco else None
    try:
        if cache_ativo:
            corpo_cache = await request.body()
            corpo_enviado = False

            async def receber_corpo_reproduzido():
                nonlocal corpo_enviado
                if not corpo_enviado:
                    corpo_enviado = True
                    return {
                        "type": "http.request",
                        "body": corpo_cache,
                        "more_body": False,
                    }
                return {"type": "http.request", "body": b"", "more_body": False}

            request._receive = receber_corpo_reproduzido
            cacheado = None
            if not response_cache.deve_forcar_atualizacao(request):
                cacheado = await response_cache.buscar_resposta(request, corpo_cache)
            if cacheado is not None:
                resposta_do_cache = True
                response = JSONResponse(
                    content=cacheado["body"],
                    status_code=cacheado["status"],
                    headers={
                        "Content-Type": cacheado["content_type"],
                        "X-Cache": "HIT",
                        "X-Request-ID": obter_request_id(request),
                    },
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
    except Exception as error:
        logger.exception(
            "Requisição HTTP falhou antes de gerar resposta",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((perf_counter() - inicio) * 1000, 2),
                "error_type": type(error).__name__,
            },
        )
        raise
    else:
        if cache_ativo and not resposta_do_cache:
            response.headers["X-Cache"] = "MISS"
            content_type = response.headers.get("content-type", "")
            if 200 <= response.status_code < 300 and "json" in content_type.lower():
                corpo_resposta = b"".join(
                    [parte async for parte in response.body_iterator]
                )
                headers = dict(response.headers)
                headers.pop("content-length", None)
                headers["X-Cache"] = "MISS"
                response = Response(
                    content=corpo_resposta,
                    status_code=response.status_code,
                    headers=headers,
                )
                await response_cache.salvar_resposta(
                    request,
                    corpo_cache,
                    status=response.status_code,
                    content_type=content_type,
                    resposta=corpo_resposta,
                )
        if request.url.path == "/health" and response.status_code < 400:
            nivel = logging.DEBUG
        else:
            nivel = logging.ERROR if response.status_code >= 500 else logging.INFO
        logger.log(
            nivel,
            "Requisição HTTP concluída",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - inicio) * 1000, 2),
            },
        )
        if medir_banco:
            medicao_banco = orm.obter_medicao_banco()
            duracao_banco_ms = float(medicao_banco["duration_ms"])
            response.headers["Server-Timing"] = (
                f'db;dur={duracao_banco_ms:.3f};desc="PostgreSQL"'
            )
            response.headers["X-Database-Queries"] = str(
                int(medicao_banco["queries"])
            )
        return response
    finally:
        if token_medicao_banco is not None:
            orm.finalizar_medicao_banco(token_medicao_banco)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=False,
    max_age=600,
)

app.include_router(router)


def _gerar_openapi_com_force_refresh() -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    parametro_force_refresh = {
        "name": "X-Force-Refresh",
        "in": "header",
        "required": False,
        "description": (
            "Defina como true para ignorar o cache, recalcular a resposta e "
            "atualizar a entrada armazenada."
        ),
        "schema": {"type": "boolean", "default": False},
    }
    for caminho, operacoes in schema.get("paths", {}).items():
        if not caminho.startswith(("/pipeline/", "/pncp/")):
            continue
        for metodo, operacao in operacoes.items():
            if metodo not in {"get", "post"}:
                continue
            parametros = operacao.setdefault("parameters", [])
            if not any(
                item.get("in") == "header"
                and item.get("name", "").lower() == "x-force-refresh"
                for item in parametros
            ):
                parametros.append(parametro_force_refresh)

    app.openapi_schema = schema
    return schema


app.openapi = _gerar_openapi_com_force_refresh
