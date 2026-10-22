from decimal import Decimal
import logging
import time
from typing import Any, Callable

import requests

from app.core.logging import executar_com_log_ingestao, registrar_falha_ingestao
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, TCE_CE_BASE_URL
from app.pipeline.ingestion.pagination import LIMITE_REGISTROS_POR_REQUISICAO, listar_por_start_index
from app.pipeline.naturezas_despesa import codigo_natureza_despesa_monitorado
from app.utils import normalizar_data_iso

BASE_URL = TCE_CE_BASE_URL
TAMANHO_PAGINA = LIMITE_REGISTROS_POR_REQUISICAO
CODIGO_MUNICIPIO_PADRAO = CODIGO_MUNICIPIO_TCE_PADRAO

logger = logging.getLogger(__name__)


class TceIndisponivelError(RuntimeError):
    """A fonte TCE-CE falhou; uma resposta vazia não pode ser presumida."""


class TceRespostaInvalidaError(TceIndisponivelError):
    """A resposta do TCE-CE não satisfaz o contrato mínimo esperado."""


ENDPOINT_CONTRATACOES = "processos_administrativos_contratacoes"
ENDPOINT_CONTRATOS = "contratos"
ENDPOINT_CONTRATADOS = "contratados"
ENDPOINT_ITENS = "itens_compoem_bens_servicos"
ENDPOINT_NOTAS_EMPENHOS = "notas_empenhos"
ENDPOINT_NOTAS_ANULACOES_EMPENHOS = "notas_anulacoes_empenhos"
ENDPOINT_ORGAOS = "orgaos"
ENDPOINT_UNIDADES_ORCAMENTAIS = "unidades_orcamentais"
ENDPOINT_NOTAS_PAGAMENTOS = "notas_pagamentos"
ENDPOINT_ESTORNO_PAGAMENTOS = "estorno_pagamentos"


def normalizar_data_tce(data: str) -> str:
    return normalizar_data_iso(data, "%Y-%m-%d")


def filtrar_naturezas_despesa(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Descarta registros cujo codigo de natureza/elemento nao esta no escopo."""
    filtrados: list[dict[str, Any]] = []
    for registro in registros:
        codigo = registro.get("codigo_elemento_despesa")
        if codigo_natureza_despesa_monitorado(codigo):
            filtrados.append(registro)
    return filtrados


def _validar_resposta_tce(dados: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(dados, dict):
        raise TceRespostaInvalidaError(
            f"Resposta inválida do TCE-CE em {endpoint}: objeto JSON esperado."
        )
    if "elements" not in dados:
        raise TceRespostaInvalidaError(
            f"Resposta inválida do TCE-CE em {endpoint}: campo elements ausente."
        )
    if not isinstance(dados["elements"], list):
        raise TceRespostaInvalidaError(
            f"Resposta inválida do TCE-CE em {endpoint}: elements deve ser uma lista."
        )
    return dados


def buscar_dados_tce(
    endpoint: str,
    params: dict[str, Any],
    max_retries: int = 3,
    *,
    preservar_decimais: bool = False,
    falhar_ao_esgotar: bool = False,
) -> dict[str, Any]:
    url = f"{BASE_URL}/{endpoint}"
    params = {**params, "$format": "json"}
    espera = 1.0

    for tentativa in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=(10, 30))

            if response.status_code == 204:
                raise TceRespostaInvalidaError(
                    f"Resposta inválida do TCE-CE em {endpoint}: HTTP 204 sem conteúdo."
                )

            if response.status_code in {429, 500, 502, 503, 504} and tentativa < max_retries:
                logger.info(
                    "Resposta transitória do TCE-CE; nova tentativa agendada",
                    extra={
                        "source": "TCE-CE",
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "attempt": tentativa + 1,
                        "max_attempts": max_retries + 1,
                        "retry_in_seconds": espera,
                    },
                )
                time.sleep(espera)
                espera *= 2
                continue

            response.raise_for_status()
            dados = response.json(parse_float=Decimal) if preservar_decimais else response.json()
            return _validar_resposta_tce(dados, endpoint)

        except (requests.RequestException, ValueError) as error:
            if tentativa == max_retries:
                registrar_falha_ingestao(error)
                logger.warning(
                    "Tentativas de consulta ao TCE-CE esgotadas",
                    extra={
                        "source": "TCE-CE",
                        "endpoint": endpoint,
                        "attempts": max_retries + 1,
                        "error_type": type(error).__name__,
                    },
                )
                if falhar_ao_esgotar:
                    raise TceIndisponivelError(
                        f"TCE-CE indisponível após {max_retries + 1} tentativa(s): {endpoint}."
                    ) from error
                return {"elements": []}

            logger.info(
                "Falha transitória ao consultar TCE-CE; nova tentativa agendada",
                extra={
                    "source": "TCE-CE",
                    "endpoint": endpoint,
                    "attempt": tentativa + 1,
                    "max_attempts": max_retries + 1,
                    "retry_in_seconds": espera,
                    "error_type": type(error).__name__,
                },
            )
            time.sleep(espera)
            espera *= 2

    return {"elements": []}


def listar_registros(
    endpoint: str,
    params: dict[str, Any],
    *,
    filtrar_por_codigo_despesa: bool = False,
    preservar_decimais: bool = False,
    falhar_ao_esgotar: bool = False,
    ao_coletar_pagina: Callable[[str, int, list[Any]], None] | None = None,
) -> list[dict[str, Any]]:
    def buscar_pagina(start_index: int, tamanho_pagina: int) -> list[Any]:
        opcoes_requisicao: dict[str, bool] = {}
        if preservar_decimais:
            opcoes_requisicao["preservar_decimais"] = True
        if falhar_ao_esgotar:
            opcoes_requisicao["falhar_ao_esgotar"] = True
        pagina = buscar_dados_tce(
            endpoint,
            {
                **params,
                "$count": tamanho_pagina,
                "$start_index": start_index,
            },
            **opcoes_requisicao,
        )["elements"]
        if ao_coletar_pagina is not None:
            ao_coletar_pagina(endpoint, start_index, pagina)
        return pagina

    registros = listar_por_start_index(buscar_pagina, tamanho_pagina=TAMANHO_PAGINA)
    if not filtrar_por_codigo_despesa:
        return registros
    return filtrar_naturezas_despesa(registros)


def buscar_municipios() -> list[dict[str, Any]]:
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_municipios",
        parametros={},
        totais={"endpoint": "municipios"},
        executar=lambda: listar_registros("municipios", {}, filtrar_por_codigo_despesa=False),
    )


def _params_periodo(data_inicial: str, data_final: str, codigo_municipio: str) -> dict[str, str]:
    return {
        "codigo_municipio": codigo_municipio,
        "data_inicio": normalizar_data_tce(data_inicial),
        "data_fim": normalizar_data_tce(data_final),
    }


def _normalizar_competencia_documental(competencia: str) -> tuple[int, int]:
    if not isinstance(competencia, str):
        raise TypeError(f"Competencia deve ser str, nao {type(competencia).__name__}.")

    texto = competencia.strip()
    if len(texto) != 7 or texto[4] != "-" or not (texto[:4] + texto[5:]).isdigit():
        raise ValueError(f"Competencia invalida: {competencia!r}. Use YYYY-MM.")

    ano = int(texto[:4])
    mes = int(texto[5:])
    if ano < 1 or mes < 1 or mes > 12:
        raise ValueError(f"Competencia invalida: {competencia!r}. Use YYYY-MM.")

    return ano, mes


def _normalizar_exercicio_orcamento(exercicio_orcamento: int | str) -> int:
    if isinstance(exercicio_orcamento, bool):
        raise TypeError("exercicio_orcamento deve usar o formato AAAA00.")

    texto = str(exercicio_orcamento).strip()
    if len(texto) != 6 or not texto.isdigit() or texto[-2:] != "00":
        raise ValueError("exercicio_orcamento deve usar o formato AAAA00.")

    exercicio = int(texto)
    if exercicio < 100000:
        raise ValueError("exercicio_orcamento deve usar o formato AAAA00.")
    return exercicio


def parametros_documento_tce(
    competencia_documental: str,
    exercicio_orcamento: int | str,
    codigo_municipio: str,
) -> dict[str, str | int]:
    """Monta os parâmetros DCD sem acoplar orçamento e competência documental."""
    ano_documento, mes_documento = _normalizar_competencia_documental(
        competencia_documental
    )
    exercicio = _normalizar_exercicio_orcamento(exercicio_orcamento)

    municipio = str(codigo_municipio).strip()
    if not municipio:
        raise ValueError("codigo_municipio nao pode ser vazio.")

    return {
        "codigo_municipio": municipio,
        "exercicio_orcamento": exercicio,
        "data_referencia_doc": ano_documento * 100 + mes_documento,
    }


def parametros_competencia_tce(
    competencia: str,
    codigo_municipio: str,
) -> dict[str, str | int]:
    """Mantém o contrato legado em que orçamento e competência usam o mesmo ano."""
    ano, _ = _normalizar_competencia_documental(competencia)
    return parametros_documento_tce(
        competencia,
        ano * 100,
        codigo_municipio,
    )


def _buscar_documentos_dcd(
    endpoint: str,
    etapa: str,
    competencia: str,
    codigo_municipio: str,
) -> list[dict[str, Any]]:
    parametros_api = parametros_competencia_tce(competencia, codigo_municipio)
    parametros_log = {
        "competencia": competencia,
        "codigo_municipio": str(codigo_municipio).strip(),
        "exercicio_orcamento": parametros_api["exercicio_orcamento"],
        "data_referencia_doc": parametros_api["data_referencia_doc"],
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa=etapa,
        parametros=parametros_log,
        totais={"endpoint": endpoint},
        executar=lambda: listar_registros(
            endpoint,
            parametros_api,
            filtrar_por_codigo_despesa=False,
            preservar_decimais=True,
            falhar_ao_esgotar=True,
        ),
    )


def _buscar_documentos_dcd_com_orcamento(
    endpoint: str,
    etapa: str,
    competencia_documental: str,
    exercicio_orcamento: int | str,
    codigo_municipio: str,
    ao_coletar_pagina: Callable[[str, int, list[Any]], None] | None = None,
) -> list[dict[str, Any]]:
    parametros_api = parametros_documento_tce(
        competencia_documental,
        exercicio_orcamento,
        codigo_municipio,
    )
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa=etapa,
        parametros={
            "competencia": competencia_documental,
            **parametros_api,
        },
        totais={"endpoint": endpoint},
        executar=lambda: listar_registros(
            endpoint,
            parametros_api,
            filtrar_por_codigo_despesa=False,
            preservar_decimais=True,
            falhar_ao_esgotar=True,
            **({"ao_coletar_pagina": ao_coletar_pagina} if ao_coletar_pagina else {}),
        ),
    )


def buscar_notas_empenhos(
    competencia: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    """Coleta todas as notas de empenho de um município em uma competência."""
    return _buscar_documentos_dcd(
        ENDPOINT_NOTAS_EMPENHOS,
        "buscar_notas_empenhos",
        competencia,
        codigo_municipio,
    )


def buscar_notas_anulacoes_empenhos(
    competencia: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    """Coleta todas as anulações de empenho de um município em uma competência."""
    return _buscar_documentos_dcd(
        ENDPOINT_NOTAS_ANULACOES_EMPENHOS,
        "buscar_notas_anulacoes_empenhos",
        competencia,
        codigo_municipio,
    )


def buscar_orgaos(
    exercicio_orcamento: int | str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    *,
    ao_coletar_pagina: Callable[[str, int, list[Any]], None] | None = None,
) -> list[dict[str, Any]]:
    exercicio = _normalizar_exercicio_orcamento(exercicio_orcamento)
    municipio = str(codigo_municipio).strip()
    if not municipio:
        raise ValueError("codigo_municipio nao pode ser vazio.")
    parametros = {
        "codigo_municipio": municipio,
        "exercicio_orcamento": exercicio,
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_orgaos",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_ORGAOS},
        executar=lambda: listar_registros(
            ENDPOINT_ORGAOS,
            parametros,
            preservar_decimais=True,
            falhar_ao_esgotar=True,
            **({"ao_coletar_pagina": ao_coletar_pagina} if ao_coletar_pagina else {}),
        ),
    )


def buscar_notas_pagamentos(
    competencia_documental: str,
    exercicio_orcamento: int | str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    *,
    ao_coletar_pagina: Callable[[str, int, list[Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Coleta pagamentos, inclusive quando o documento é posterior ao orçamento."""
    return _buscar_documentos_dcd_com_orcamento(
        ENDPOINT_NOTAS_PAGAMENTOS,
        "buscar_notas_pagamentos",
        competencia_documental,
        exercicio_orcamento,
        codigo_municipio,
        ao_coletar_pagina,
    )


def buscar_contratacoes(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    modalidade: str = "",
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
        "modalidade": modalidade,
    }

    def executar() -> list[dict[str, Any]]:
        registros = listar_registros(
            ENDPOINT_CONTRATACOES,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        )

        if not modalidade:
            return registros

        return [registro for registro in registros if registro.get("modalidade_licitacao") == modalidade]

    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratacoes",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATACOES},
        executar=executar,
    )


def buscar_contratos(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratos",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATOS},
        executar=lambda: listar_registros(
            ENDPOINT_CONTRATOS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
            filtrar_por_codigo_despesa=False,
        ),
    )


def buscar_contratados(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
    }
    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_contratados",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_CONTRATADOS},
        executar=lambda: listar_registros(
            ENDPOINT_CONTRATADOS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
            filtrar_por_codigo_despesa=False,
        ),
    )


def buscar_itens_contratacao(
    data_inicial: str,
    data_final: str,
    codigo_municipio: str = CODIGO_MUNICIPIO_PADRAO,
    numero_licitacao: str = "",
) -> list[dict[str, Any]]:
    parametros = {
        "data_inicial": data_inicial,
        "data_final": data_final,
        "codigo_municipio": codigo_municipio,
        "numero_licitacao": numero_licitacao,
    }

    def executar() -> list[dict[str, Any]]:
        registros = listar_registros(
            ENDPOINT_ITENS,
            _params_periodo(data_inicial, data_final, codigo_municipio),
        )

        if not numero_licitacao:
            return registros

        return [registro for registro in registros if registro.get("numero_licitacao") == numero_licitacao]

    return executar_com_log_ingestao(
        fonte="TCE-CE",
        etapa="buscar_itens_contratacao",
        parametros=parametros,
        totais={"endpoint": ENDPOINT_ITENS},
        executar=executar,
    )
