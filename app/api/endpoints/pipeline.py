import logging
from calendar import monthrange
from datetime import date
from typing import Annotated, Any
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import StringConstraints
from app.api.errors import REQUEST_ID_HEADER, obter_request_id, resposta_erro
from app.api import response_cache
from app.api.schemas.comparisons import CompanySize, SupplierOrigin
from app.api.schemas.empenhos import EmpenhoSortField, SortOrder
from app.api.schemas.fornecedores import SupplierDocumentType, SupplierSortField
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analisys, comparisons, empenhos, fornecedores
from app.pipeline.kpis import DadosInsuficientesKPI
from app.utils import DATA_ISO_FORMATO, normalizar_data_iso


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/pipeline",
    responses={
        422: {
            "description": "Parametros invalidos ou dados insuficientes para o processamento.",
        },
        503: {"description": "Snapshot local indisponivel para o recorte solicitado."},
    },
)

DATA_ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
CodigoMunicipioTce = Annotated[str, StringConstraints(pattern=r"^\d{3}$")]


def _data_inicial_query():
    return Query(description=f"Data inicial em {DATA_ISO_FORMATO}.", pattern=DATA_ISO_PATTERN)


def _data_final_query():
    return Query(description=f"Data final em {DATA_ISO_FORMATO}.", pattern=DATA_ISO_PATTERN)


def _validar_periodo_api(data_inicial: str, data_final: str) -> None:
    normalizar_data_iso(data_inicial, "%Y-%m-%d")
    normalizar_data_iso(data_final, "%Y-%m-%d")


def _erro_pipeline(error: Exception) -> HTTPException:
    if isinstance(error, (ValueError, TypeError, DadosInsuficientesKPI)):
        logger.info(
            "Pipeline rejeitou os parâmetros ou os dados disponíveis",
            extra={
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, analisys.CompetenciasTceIndisponiveisError):
        logger.warning(
            "Pipeline indisponível por ausência de snapshot local",
            extra={
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        return HTTPException(status_code=503, detail=str(error))
    logger.exception(
        "Falha inesperada ao executar o pipeline",
        extra={
            "error_type": type(error).__name__,
        },
    )
    return HTTPException(status_code=500, detail="Falha inesperada ao executar o pipeline.")


@router.get(
    "/tce/kpis/portes-por-mes",
    tags=["Indicadores TCE-CE"],
    summary="Calcula participação por porte empresarial por mês",
    description=(
        "Calcula quantidade, valor pago e percentual mensal por porte empresarial "
        "usando notas de pagamento nao estornadas publicadas no banco local."
    ),
    response_description=(
        "Objeto com `dados`, uma série mensal por porte contendo quantidade, valor "
        "pago e percentual; inclui `totais`, competências processadas, "
        "parâmetros, `limite_resposta` e metadados comuns de disponibilidade, "
        "defasagem e qualidade. Valores monetários usam centavos."
    ),
)
def tce_kpi_portes_por_mes(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str,
        Query(description="Código do município no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    exercicio_orcamentario: Annotated[
        int | None,
        Query(
            description="Ano do orçamento, independente da data do pagamento.",
            ge=1900,
            le=9999,
        ),
    ] = None,
    limite: Annotated[
        int | None,
        Query(
            description="Limite de registros retornados.",
            ge=1,
            le=1000,
        ),
    ] = 100,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_kpi_tce_portes_por_mes(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            exercicio_orcamentario=exercicio_orcamentario,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


def _resolver_periodo_pagamentos(
    *,
    exercicio: int,
    referencia_final: str | None,
    data_inicial: str | None,
    data_final: str | None,
) -> tuple[str, str]:
    if referencia_final is not None and (data_inicial is not None or data_final is not None):
        raise ValueError(
            "Use referencia_final ou o par data_inicial/data_final, nao ambos."
        )
    if referencia_final is not None:
        try:
            ano, mes = map(int, referencia_final.split("-"))
            ultimo_dia = monthrange(ano, mes)[1]
        except (TypeError, ValueError) as error:
            raise ValueError("referencia_final deve usar o formato YYYY-MM.") from error
        return f"{exercicio:04d}-01-01", f"{ano:04d}-{mes:02d}-{ultimo_dia:02d}"
    if data_inicial is None or data_final is None:
        raise ValueError(
            "Informe referencia_final ou ambos data_inicial e data_final."
        )
    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
    if inicio > fim:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")
    return inicio.isoformat(), fim.isoformat()


@router.get(
    "/tce/pagamentos/overview",
    tags=["Pagamentos TCE-CE"],
    summary="Consulta pagamentos da Prefeitura por orçamento e período",
    description=(
        "Soma valor_nota_pagamento de notas nao estornadas, exclui a Camara pelo "
        "cadastro de orgaos e aplica os sete elementos monitorados. O exercicio "
        "orcamentario e independente do periodo, que incide sobre data_nota_pagamento "
        "com limites inclusivos. Usa somente snapshots locais publicados."
    ),
)
def tce_pagamentos_overview(
    codigo_municipio: Annotated[str, Query(pattern=r"^[0-9]{3}$")],
    exercicio: Annotated[int, Query(ge=1900, le=9999)],
    referencia_final: Annotated[
        str | None,
        Query(
            pattern=r"^[0-9]{4}-[0-9]{2}$",
            description=(
                "Compatibilidade: consulta de janeiro do exercicio ate o ultimo "
                "dia do mes informado."
            ),
        ),
    ] = None,
    data_inicial: Annotated[str | None, Query(pattern=DATA_ISO_PATTERN)] = None,
    data_final: Annotated[str | None, Query(pattern=DATA_ISO_PATTERN)] = None,
) -> dict[str, Any]:
    try:
        inicio, fim = _resolver_periodo_pagamentos(
            exercicio=exercicio,
            referencia_final=referencia_final,
            data_inicial=data_inicial,
            data_final=data_final,
        )
        return analisys.consultar_overview_pagamentos_tce(
            data_inicial=inicio,
            data_final=fim,
            codigo_municipio=codigo_municipio,
            exercicio_orcamentario=exercicio,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview",
    tags=["Indicadores TCE-CE"],
    summary="Consulta o overview estadual de Microempresas (ME)",
    description=(
        "Calcula o overview estadual com notas de pagamento nao estornadas. "
        "Empenhos associados fornecem somente as dimensoes de natureza e fornecedor. "
        "Usa snapshots locais e nao consulta fontes externas."
    ),
    response_description=(
        "Objeto com KPIs e totais em centavos, evolução mensal, distribuição por porte "
        "e destino geográfico dos recursos, além de escopo, parâmetros, cobertura das "
        "competências e metadados de qualidade/defasagem."
    ),
)
def tce_overview(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str | None,
        Query(
            description=(
                "Código do município no TCE-CE. Quando omitido, consolida "
                "todos os municípios do Ceará com lotes publicados."
            )
        ),
    ] = None,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_overview_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview/municipal",
    tags=["Indicadores TCE-CE"],
    summary="Consulta o overview de um municipio por periodo",
    description=(
        "Calcula o overview municipal com notas de pagamento nao estornadas para "
        "o exercicio orcamentario do ano de data_inicial e o periodo civil solicitado. "
        "Empenhos associados fornecem somente as dimensoes de natureza e fornecedor. "
        "Por padrao mostra todos os portes; company_sizes restringe o calculo."
    ),
    response_description=(
        "Objeto com KPIs e totais em centavos, evolução mensal, distribuição por porte "
        "e destino geográfico para o município; também retorna filtro aplicado, escopo, "
        "competências e metadados de qualidade/defasagem."
    ),
)
def tce_overview_municipal(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio_tce: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE.", pattern=r"^\d{3}$"),
    ],
    company_sizes: Annotated[
        list[CompanySize] | None,
        Query(
            description=(
                "Portes empresariais repetiveis. Quando omitido, inclui "
                "MEI, ME, EPP, OTHER e UNKNOWN."
            )
        ),
    ] = None,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        filtros_portes = [item.value for item in company_sizes or []]
        resultado = analisys.consultar_overview_municipal_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio_tce,
            company_sizes=filtros_portes,
        )
        return {
            **resultado,
            "parametros": {
                **dict(resultado.get("parametros") or {}),
                "data_inicial": data_inicial,
                "data_final": data_final,
                "codigo_municipio": codigo_municipio_tce,
                "company_sizes": filtros_portes,
            },
        }
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview/subscriber",
    tags=["Indicadores TCE-CE"],
    summary="Consulta participação de ME por natureza da despesa (assinante)",
    description=(
        "Visão estadual para assinantes. Calcula ME / (ME + MEI + EPP + DEMAIS) "
        "por natureza padronizada, em centavos. Naturezas não classificáveis são "
        "retornadas em grupo próprio e denominadores zero têm percentual nulo."
    ),
)
def tce_overview_subscriber(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_participacao_me_por_natureza_tce(
            data_inicial=data_inicial,
            data_final=data_final,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/overview/municipal/subscriber",
    tags=["Indicadores TCE-CE"],
    summary="Consulta participação municipal de ME por natureza (assinante)",
    description=(
        "Visão municipal para assinantes, com período e filtro opcional de portes. "
        "Percentuais usam ME como numerador e a soma dos portes selecionados como denominador."
    ),
)
def tce_overview_municipal_subscriber(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio_tce: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE.", pattern=r"^\d{3}$"),
    ],
    company_sizes: Annotated[
        list[CompanySize] | None,
        Query(description="Portes considerados no denominador; repetivel."),
    ] = None,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        filtros_portes = [item.value for item in company_sizes or []]
        return analisys.consultar_participacao_me_por_natureza_tce(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio_tce,
            company_sizes=filtros_portes,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


@router.get(
    "/tce/comparisons",
    tags=["Comparacoes TCE-CE"],
    summary="Compara pagamentos de dois a quatro municipios do TCE-CE",
    description=(
        "Selecione de 2 a 4 municipios repetindo `codigo_municipio_tce` na query. "
        "Cada codigo TCE deve ter tres digitos, ser unico e existir no catalogo local. "
        "Informe tambem `data_inicial` e `data_final` inclusivas no formato YYYY-MM-DD. "
        "O periodo civil filtra a data do pagamento; o exercicio orcamentario usado "
        "e o ano de `data_inicial`. Valores financeiros usam pagamentos publicados "
        "nao estornados. Empenhos associados servem apenas como dimensoes de natureza "
        "da despesa e fornecedor. A operacao e somente leitura.\n\n"
        "Exemplo: `/pipeline/tce/comparisons?codigo_municipio_tce=004&codigo_municipio_tce=010&data_inicial=2025-01-01&data_final=2025-12-31`."
    ),
    response_description=(
        "Retorna `municipios` com o overview individual e `comparacao` com valores "
        "por municipio para KPIs, portes, elementos de despesa, meses e destinos. "
        "O campo `medida` identifica valores de pagamentos nao estornados."
    ),
    response_model=None,
    responses={
        200: {
            "description": "Comparacao dos pagamentos publicados para os municipios selecionados.",
            "content": {
                "application/json": {
                    "example": {
                        "fonte": "TCE-CE/SIM",
                        "origem_dados": "BANCO_LOCAL",
                        "consultas_externas": False,
                        "data_available": True,
                        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
                        "parametros": {
                            "codigo_municipio_tce": ["004", "010"],
                            "data_inicial": "2025-01-01",
                            "data_final": "2025-12-31",
                            "exercicio_orcamentario": 2025,
                        },
                        "municipios": [
                            {
                                "codigo_municipio_tce": "004",
                                "nome": "Acaraú",
                                "overview": {
                                    "kpis": {"total_compras_centavos": 125000}
                                },
                            },
                            {
                                "codigo_municipio_tce": "010",
                                "nome": "Município exemplo",
                                "overview": {
                                    "kpis": {"total_compras_centavos": 98000}
                                },
                            }
                        ],
                        "comparacao": {
                            "kpis": [
                                {
                                    "indicador": "total_compras_centavos",
                                    "unidade": "CENTAVOS",
                                    "valores": [
                                        {
                                            "codigo_municipio_tce": "004",
                                            "municipio": "Acaraú",
                                            "valor": 125000,
                                        },
                                        {
                                            "codigo_municipio_tce": "010",
                                            "municipio": "Município exemplo",
                                            "valor": 98000,
                                        }
                                    ],
                                }
                            ],
                            "participacao_por_porte_empresarial": [],
                            "elementos_despesa": [],
                            "evolucao_compras_consideradas": [],
                            "destino_recursos": [],
                        },
                    }
                }
            },
        },
        404: {"description": "Municipio nao encontrado no catalogo local."},
        422: {"description": "Informe 2 a 4 codigos TCE unicos e validos, datas validas e snapshots completos."},
        500: {"description": "Falha inesperada ao consultar o banco local."},
    },
)
def tce_comparisons(
    codigo_municipio_tce: Annotated[
        list[CodigoMunicipioTce],
        Query(
            min_length=2,
            max_length=4,
            description="Repita para cada municipio TCE, por exemplo 004, 010, 157 e 057.",
        ),
    ],
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    request: Request,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return comparisons.consultar_comparacao_tce(
            codigo_municipio_tce=codigo_municipio_tce,
            data_inicial=data_inicial,
            data_final=data_final,
            force_refresh=response_cache.deve_forcar_atualizacao(request),
        )
    except comparisons.ComparisonRequestError as error:
        logger.info(
            "Comparacao rejeitada pelos filtros ou dados locais",
            extra={
                "request_id": request_id,
                "error_code": error.code,
                "error": error.message,
            },
        )
        return resposta_erro(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
            request_id=request_id,
        )
    except Exception as error:
        logger.exception(
            "Falha inesperada ao consultar comparacao",
            extra={
                "request_id": request_id,
                "error_type": type(error).__name__,
            },
        )
        return resposta_erro(
            status_code=500,
            code="INTERNAL_ERROR",
            message="Falha inesperada ao consultar os dados da comparacao.",
            details=[],
            request_id=request_id,
        )


def _erro_empenhos(
    error: Exception,
    *,
    request_id: str,
    mensagem_interna: str,
) -> JSONResponse:
    if isinstance(error, empenhos.EmpenhoRequestError):
        logger.info(
            "Consulta local rejeitada",
            extra={"request_id": request_id, "error_code": error.code, "error": error.message},
        )
        return resposta_erro(
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            details=error.details,
            request_id=request_id,
        )
    logger.exception(
        "Falha inesperada ao consultar dados locais",
        extra={"request_id": request_id, "error_type": type(error).__name__},
    )
    return resposta_erro(
        status_code=500,
        code="INTERNAL_ERROR",
        message=mensagem_interna,
        details=[],
        request_id=request_id,
    )


@router.get(
    "/tce/despesas",
    tags=["Despesas TCE-CE"],
    summary="Lista despesas publicadas armazenadas",
    description=(
        "Lista e pagina empenhos liquidos dos sete elementos monitorados usando "
        "somente lotes publicados. valor_final_centavos e o campo legado para "
        "valor empenhado menos anulacoes; nao representa pagamentos."
    ),
    response_description=("Objeto com items de despesas, fornecedor, elemento e somente valor final em centavos, mais paginacao, ordenacao e filtros."),
    response_model=None,
)
def tce_empenhos(
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: EmpenhoSortField = EmpenhoSortField.FINAL_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return empenhos.listar_empenhos(
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os empenhos.",
        )


@router.get(
    "/tce/despesas/{chave_despesa}",
    tags=["Despesas TCE-CE"],
    summary="Detalha uma despesa publicada pela chave canonica",
    description=(
        "Retorna o empenho e seu valor liquido (empenhado menos anulacoes) em "
        "centavos. Nao representa valor pago e nao consulta fontes externas."
    ),
    response_description=("Objeto da despesa com fornecedor, municipio, valor final em centavos, classificacao orcamentaria e referencias de contrato e licitacao."),
    response_model=None,
)
def tce_empenho_detalhe(
    chave_despesa: str,
    request: Request,
    response: Response,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return empenhos.obter_empenho(chave_despesa)
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao consultar o empenho.",
        )


@router.get(
    "/tce/fornecedores",
    tags=["Fornecedores TCE-CE"],
    summary="Lista fornecedores presentes nas despesas publicadas",
    description=(
        "Agrupa fornecedores e valores do recorte usando exclusivamente dados "
        "publicados e cadastros armazenados no banco local."
    ),
    response_description=(
        "Objeto com `items` de fornecedores agregados — identificação, porte, origem, "
        "quantidades e valores em centavos —, além de `pagination`, `sorting` e `filters`."
    ),
    response_model=None,
)
def tce_fornecedores(
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: SupplierSortField = SupplierSortField.FINAL_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.listar_fornecedores(
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os fornecedores.",
        )


@router.get(
    "/tce/fornecedores/{tipo_documento}/{documento}",
    tags=["Fornecedores TCE-CE"],
    summary="Consulta o resumo analitico de um fornecedor",
    description=(
        "Retorna cadastro armazenado, indicadores, evolucao mensal, elementos "
        "de despesa e participacao do fornecedor no recorte."
    ),
    response_description=(
        "Objeto com `fornecedor` e cadastro disponível, `recorte` financeiro em centavos, "
        "`evolucao_mensal`, `por_elemento_despesa`, participação no total e referência paginável aos empenhos."
    ),
    response_model=None,
)
def tce_fornecedor_detalhe(
    tipo_documento: SupplierDocumentType,
    documento: str,
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.obter_fornecedor(
            tipo_documento=tipo_documento.value,
            documento=documento,
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao consultar o fornecedor.",
        )


@router.get(
    "/tce/fornecedores/{tipo_documento}/{documento}/despesas",
    tags=["Fornecedores TCE-CE"],
    summary="Lista as despesas publicadas de um fornecedor",
    description=(
        "Pagina as notas consideradas para o fornecedor e recorte sem recalcular "
        "os graficos da rota de detalhe."
    ),
    response_description=(
        "Objeto com a identificação do fornecedor, `items` de empenhos e seus valores em "
        "centavos, além de `pagination`, `sorting` e `filters` aplicados."
    ),
    response_model=None,
)
def tce_fornecedor_empenhos(
    tipo_documento: SupplierDocumentType,
    documento: str,
    request: Request,
    response: Response,
    start_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    end_date: Annotated[str, Query(pattern=DATA_ISO_PATTERN)],
    uf: Annotated[str, Query(min_length=2, max_length=2)] = UF_PADRAO,
    municipality_tce_code: Annotated[str | None, Query(pattern=r"^\d{3}$")] = None,
    company_sizes: Annotated[list[CompanySize] | None, Query()] = None,
    supplier_origins: Annotated[list[SupplierOrigin] | None, Query()] = None,
    expense_element_codes: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    sort_by: EmpenhoSortField = EmpenhoSortField.FINAL_VALUE,
    sort_order: SortOrder = SortOrder.DESC,
) -> dict[str, Any] | JSONResponse:
    request_id = obter_request_id(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    try:
        return fornecedores.listar_empenhos_fornecedor(
            tipo_documento=tipo_documento.value,
            documento=documento,
            start_date=start_date,
            end_date=end_date,
            uf=uf,
            municipality_tce_code=municipality_tce_code,
            company_sizes=[item.value for item in company_sizes or []],
            supplier_origins=[item.value for item in supplier_origins or []],
            expense_element_codes=expense_element_codes or [],
            search=search,
            page=page,
            page_size=page_size,
            sort_by=sort_by.value,
            sort_order=sort_order.value,
        )
    except Exception as error:
        return _erro_empenhos(
            error,
            request_id=request_id,
            mensagem_interna="Falha inesperada ao listar os empenhos do fornecedor.",
        )


@router.get(
    "/tce/analitico/indicadores",
    tags=["Indicadores TCE-CE"],
    summary="Calcula indicadores analiticos principais do TCE-CE",
    description=(
        "Calcula indicadores sobre empenhos líquidos publicados do TCE-CE usando "
        "somente o banco local. O municipio comprador e resolvido pelo cadastro local."
    ),
    response_description=(
        "Objeto com `indicadores`, tabelas dos indicadores analíticos solicitados, `totais` "
        "por tabela, competências processadas, parâmetros, base de cálculo, `limite_resposta` "
        "e metadados comuns de disponibilidade, defasagem e qualidade."
    ),
)
def tce_analitico_indicadores(
    data_inicial: Annotated[str, _data_inicial_query()],
    data_final: Annotated[str, _data_final_query()],
    codigo_municipio: Annotated[
        str,
        Query(description="Codigo do municipio no TCE-CE."),
    ] = CODIGO_MUNICIPIO_TCE_PADRAO,
    limite_ranking: Annotated[
        int,
        Query(description="Quantidade maxima de fornecedores no ranking.", ge=1, le=100),
    ] = 10,
    limite: Annotated[
        int | None,
        Query(description="Limite de registros por tabela de indicador retornada.", ge=1, le=1000),
    ] = 100,
) -> dict[str, object]:
    try:
        _validar_periodo_api(data_inicial, data_final)
        return analisys.consultar_tce_indicadores_analiticos(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio=codigo_municipio,
            limite_ranking=limite_ranking,
            limite=limite,
        )
    except Exception as error:
        raise _erro_pipeline(error) from error


__all__ = [
    "router",
    "tce_analitico_indicadores",
    "tce_comparisons",
    "tce_empenho_detalhe",
    "tce_empenhos",
    "tce_fornecedor_detalhe",
    "tce_fornecedor_empenhos",
    "tce_fornecedores",
    "tce_kpi_portes_por_mes",
    "tce_overview",
    "tce_overview_municipal",
]
