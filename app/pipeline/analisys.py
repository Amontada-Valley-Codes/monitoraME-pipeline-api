from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
from typing import Any
import pandas as pd
from app.core import database
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analitico, kpis, merge
from app.pipeline import tce_observability
from app.pipeline import tce_despesas as tce_despesas_pipeline
from app.pipeline.cleaners import tce as tce_cleaning
from app.pipeline.enrichment.fornecedores import enriquecer_com_fornecedor, extrair_cnpjs_distintos
from app.pipeline.ingestion import fornecedores, tce
from app.pipeline.persistence import tce_despesas as tce_despesas_persistence
from app.pipeline.persistence import tce_pagamentos as tce_pagamentos_persistence


logger = logging.getLogger(__name__)


class CompetenciasTceIndisponiveisError(RuntimeError):
    """Nenhuma competência solicitada possui uma publicação utilizável."""


TTL_COMPETENCIA_MES_ATUAL = timedelta(hours=1)
TTL_COMPETENCIA_TRES_MESES_ANTERIORES = timedelta(hours=24)
TTL_COMPETENCIA_HISTORICA = timedelta(days=7)


@dataclass(frozen=True)
class ResultadoAtualizacaoCompetenciasTce:
    publicadas: list[str]
    atualizadas: list[str]
    reutilizadas: list[str]
    desatualizadas: list[str]
    ausentes: list[str]
    falhas: list[dict[str, Any]]


def valor_json(valor: Any) -> Any:
    """Converte valores pandas/numpy em tipos aceitos por JSON."""
    if valor is None:
        return None

    try:
        nulo = pd.isna(valor)
    except (TypeError, ValueError):
        nulo = False

    try:
        if not isinstance(nulo, (list, tuple)) and not getattr(nulo, "shape", None) and bool(nulo):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(valor, pd.Timestamp):
        return valor.isoformat()

    if isinstance(valor, dict):
        return {str(chave): valor_json(item) for chave, item in valor.items()}

    if isinstance(valor, (list, tuple)):
        return [valor_json(item) for item in valor]

    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, bytearray)):
        try:
            return valor.item()
        except (AttributeError, TypeError, ValueError):
            return valor

    return valor


def dataframe_para_registros(df: pd.DataFrame | None, *, limite: int | None = None) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    if limite is not None:
        df = df.head(limite)

    return [
        {str(chave): valor_json(valor) for chave, valor in registro.items()}
        for registro in df.to_dict(orient="records")
    ]


def tabelas_para_json(
    tabelas: dict[str, pd.DataFrame],
    *,
    limite: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    return {nome: dataframe_para_registros(tabela, limite=limite) for nome, tabela in tabelas.items()}


def _totais_tabelas(tabelas: dict[str, pd.DataFrame]) -> dict[str, int]:
    return {nome: int(len(tabela)) for nome, tabela in tabelas.items()}


def _ordenar_tce_contratos_por_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "data_contrato" not in df.columns:
        return df

    coluna_data_ordenacao = "_data_contrato_ordenacao"
    ordenado = df.copy()
    ordenado[coluna_data_ordenacao] = pd.to_datetime(ordenado["data_contrato"], errors="coerce", utc=True)

    colunas_ordenacao = [coluna_data_ordenacao]
    ascendentes = [True]
    if "numero_contrato" in ordenado.columns:
        colunas_ordenacao.append("numero_contrato")
        ascendentes.append(True)
    if "codigo_municipio" in ordenado.columns:
        colunas_ordenacao.append("codigo_municipio")
        ascendentes.append(True)

    return (
        ordenado.sort_values(colunas_ordenacao, ascending=ascendentes, na_position="last")
        .drop(columns=[coluna_data_ordenacao])
        .reset_index(drop=True)
    )


def _resolver_nome_municipio_tce(codigo_municipio: str) -> str:
    municipio_salvo = database.localizar_municipio_tce(codigo_municipio)
    if municipio_salvo is not None:
        return municipio_salvo["nome"]

    logger.info(
        "Município ausente no cache; sincronização do TCE-CE será executada",
        extra={
            "codigo_municipio": codigo_municipio,
        },
    )
    municipios = tce.buscar_municipios()
    database.salvar_municipios_tce(municipios, UF_PADRAO)
    codigo_procurado = str(codigo_municipio).strip()

    for municipio in municipios:
        codigo = str(municipio.get("codigo_municipio", "")).strip()
        if codigo != codigo_procurado:
            continue

        nome = municipio.get("nome_municipio") or municipio.get("nome") or municipio.get("municipio")
        if isinstance(nome, str) and nome.strip():
            return nome.strip()

    raise kpis.DadosInsuficientesKPI(
        f"[INDISPONIVEL] Nao foi possivel resolver o municipio TCE pelo codigo {codigo_municipio}."
    )


def _coletar_fornecedores(cnpjs: list[str], throttle_segundos: float) -> pd.DataFrame | None:
    if not cnpjs:
        return None

    return fornecedores.obter_fornecedores_com_cache(
        cnpjs,
        throttle_segundos=throttle_segundos,
    )


def _competencias_entre_datas(data_inicial: str, data_final: str) -> list[str]:
    try:
        inicio = date.fromisoformat(data_inicial)
        fim = date.fromisoformat(data_final)
    except (TypeError, ValueError) as error:
        raise ValueError("Datas devem usar o formato YYYY-MM-DD.") from error
    if inicio > fim:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")

    competencias: list[str] = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        competencias.append(f"{ano:04d}-{mes:02d}")
        if mes == 12:
            ano, mes = ano + 1, 1
        else:
            mes += 1
    return competencias


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_competencia(competencia: str, agora: datetime) -> timedelta:
    ano, mes = (int(parte) for parte in competencia.split("-", maxsplit=1))
    distancia_meses = (agora.year - ano) * 12 + agora.month - mes
    if distancia_meses == 0:
        return TTL_COMPETENCIA_MES_ATUAL
    if 1 <= distancia_meses <= 3:
        return TTL_COMPETENCIA_TRES_MESES_ANTERIORES
    return TTL_COMPETENCIA_HISTORICA


def _publicacao_atualizada(
    competencia: str,
    publicado_em: datetime | None,
    agora: datetime,
) -> bool:
    if publicado_em is None:
        return False
    instante_publicacao = (
        publicado_em.replace(tzinfo=timezone.utc)
        if publicado_em.tzinfo is None
        else publicado_em.astimezone(timezone.utc)
    )
    return instante_publicacao >= agora - _ttl_competencia(competencia, agora)


def _atualizar_competencias_tce(
    *,
    competencias: list[str],
    codigo_municipio: str,
) -> ResultadoAtualizacaoCompetenciasTce:
    logger.info(
        "Atualização de competências iniciada",
        extra={
            "codigo_municipio": codigo_municipio,
            "competencias_count": len(competencias),
        },
    )
    agora = _agora_utc()
    publicacoes_existentes = tce_despesas_persistence.listar_publicacoes_competencias(
        codigo_municipio_tce=codigo_municipio,
        competencias=competencias,
    )
    publicadas: list[str] = []
    atualizadas: list[str] = []
    reutilizadas: list[str] = []
    desatualizadas: list[str] = []
    ausentes: list[str] = []
    falhas: list[dict[str, Any]] = []
    for competencia in competencias:
        possui_publicacao = competencia in publicacoes_existentes
        if possui_publicacao and _publicacao_atualizada(
            competencia,
            publicacoes_existentes[competencia],
            agora,
        ):
            publicadas.append(competencia)
            reutilizadas.append(competencia)
            continue

        erro_atualizacao: str | None = None
        try:
            resultado = tce_despesas_pipeline.executar_ingestao_competencia_tce(
                competencia,
                codigo_municipio_tce=codigo_municipio,
            )
        except Exception as error:
            erro_atualizacao = str(error)
        else:
            if resultado.status != tce_despesas_persistence.STATUS_PUBLICADO:
                codigos = ", ".join(
                    str(problema.get("codigo", "ERRO_VALIDACAO"))
                    for problema in resultado.problemas
                )
                erro_atualizacao = codigos or f"Lote finalizado com status {resultado.status}."

        if erro_atualizacao is None:
            publicadas.append(competencia)
            atualizadas.append(competencia)
            continue

        usou_versao_publicada = possui_publicacao
        falhas.append(
            {
                "competencia": competencia,
                "erro": erro_atualizacao,
                "usou_versao_publicada": usou_versao_publicada,
            }
        )
        if usou_versao_publicada:
            publicadas.append(competencia)
            desatualizadas.append(competencia)
        else:
            ausentes.append(competencia)

        logger.warning(
            "Falha ao atualizar competência do TCE-CE",
            extra={
                "codigo_municipio": codigo_municipio,
                "competencia": competencia,
                "used_previous_version": usou_versao_publicada,
                "error": erro_atualizacao,
            },
        )

    if not publicadas:
        meses = ", ".join(competencias)
        logger.error(
            "Nenhuma competência solicitada possui publicação utilizável",
            extra={
                "codigo_municipio": codigo_municipio,
                "competencias_count": len(competencias),
            },
        )
        raise CompetenciasTceIndisponiveisError(
            f"Nenhuma competência do TCE-CE possui publicação utilizável: {meses}."
        )
    logger.info(
        "Atualização de competências concluída",
        extra={
            "codigo_municipio": codigo_municipio,
            "published_count": len(publicadas),
            "updated_count": len(atualizadas),
            "reused_count": len(reutilizadas),
            "stale_count": len(desatualizadas),
            "missing_count": len(ausentes),
            "failed_count": len(falhas),
        },
    )
    return ResultadoAtualizacaoCompetenciasTce(
        publicadas=publicadas,
        atualizadas=atualizadas,
        reutilizadas=reutilizadas,
        desatualizadas=desatualizadas,
        ausentes=ausentes,
        falhas=falhas,
    )


def montar_base_tce_contratos(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    enriquecer_fornecedores: bool = False,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    contratos_brutos = tce.buscar_contratos(data_inicial, data_final, codigo_municipio=codigo_municipio)
    contratados_brutos = tce.buscar_contratados(data_inicial, data_final, codigo_municipio=codigo_municipio)

    df_contratos = tce_cleaning.limpar(contratos_brutos, chave_duplicata=["numero_contrato", "codigo_municipio"])
    df_contratados = tce_cleaning.limpar(
        contratados_brutos,
        chave_duplicata=["numero_contrato", "codigo_municipio", "numero_documento_negociante"],
    )

    fornecedores_df = None
    if enriquecer_fornecedores:
        serie_cnpj = (
            df_contratados["numero_documento_negociante"]
            if "numero_documento_negociante" in df_contratados.columns
            else None
        )
        cnpjs = extrair_cnpjs_distintos(serie_cnpj)
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)

    base = merge.montar_base_tce(df_contratos, df_contratados, fornecedores_df=fornecedores_df)
    base = _ordenar_tce_contratos_por_data(base)
    logger.info(
        "Base de contratos do TCE-CE montada",
        extra={
            "contratos_raw_count": len(contratos_brutos),
            "contratados_raw_count": len(contratados_brutos),
            "output_count": len(base),
            "supplier_enrichment": enriquecer_fornecedores,
        },
    )
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
            "enriquecer_fornecedores": enriquecer_fornecedores,
        },
        "totais_brutos": {
            "contratos": len(contratos_brutos),
            "contratados": len(contratados_brutos),
        },
    }
    return base, metadados


def consultar_tce_contratos(*, limite: int | None = 100, **kwargs: Any) -> dict[str, Any]:
    base, metadados = montar_base_tce_contratos(**kwargs)
    return {
        **metadados,
        "limite_resposta": limite,
        "totais": {"contratos": int(len(base))},
        "dados": dataframe_para_registros(base, limite=limite),
    }


def montar_base_analitica_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    municipio_comprador: str | None = None,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    competencias = _competencias_entre_datas(data_inicial, data_final)
    atualizacao = _atualizar_competencias_tce(
        competencias=competencias,
        codigo_municipio=codigo_municipio,
    )
    municipio_comprador = municipio_comprador or _resolver_nome_municipio_tce(codigo_municipio)
    empenhos = tce_despesas_persistence.listar_empenhos_liquidos_publicados(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
    )
    competencias_validas = set(atualizacao.publicadas)
    empenhos = [
        empenho
        for empenho in empenhos
        if str(empenho.get("data_empenho", ""))[:7] in competencias_validas
    ]

    base = pd.DataFrame(empenhos)
    fornecedores_df = None
    if not base.empty:
        cnpjs = extrair_cnpjs_distintos(base.get("cnpj_fornecedor"))
        fornecedores_df = _coletar_fornecedores(cnpjs, throttle_fornecedores)
        if fornecedores_df is not None:
            base = enriquecer_com_fornecedor(
                base,
                fornecedores_df,
                coluna_cnpj="cnpj_fornecedor",
            )

    base_analitica = analitico.montar_base_analitica_tce(
        base,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
        uf_comprador=UF_PADRAO,
    )
    logger.info(
        "Base analítica do TCE-CE montada",
        extra={
            "input_count": len(empenhos),
            "output_count": len(base_analitica),
            "competencias_count": len(atualizacao.publicadas),
        },
    )
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
        },
        "competencias_solicitadas": competencias,
        "competencias_publicadas": atualizacao.publicadas,
        "competencias_atualizadas": atualizacao.atualizadas,
        "competencias_reutilizadas": atualizacao.reutilizadas,
        "competencias_desatualizadas": atualizacao.desatualizadas,
        "competencias_ausentes": atualizacao.ausentes,
        "falhas_competencias": atualizacao.falhas,
        "politica_atualizacao": {
            "ttl_mes_atual_segundos": int(TTL_COMPETENCIA_MES_ATUAL.total_seconds()),
            "ttl_tres_meses_anteriores_segundos": int(
                TTL_COMPETENCIA_TRES_MESES_ANTERIORES.total_seconds()
            ),
            "ttl_historico_segundos": int(TTL_COMPETENCIA_HISTORICA.total_seconds()),
            "usar_publicacao_anterior_em_falha": True,
        },
        "totais": {"despesas": len(empenhos)},
        "base_analitica": {
            "nome": "despesas_tce",
            "base_calculo": "DESPESA_FINAL",
            "municipio_comprador": municipio_comprador,
            "colunas": list(analitico.COLUNAS_BASE_ANALITICA_TCE),
        },
    }
    return base_analitica, metadados


def montar_base_analitica_tce_somente_banco(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str,
    municipio_comprador: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Calcula a base analítica sem atualizar lotes ou consultar fontes externas."""
    competencias = _competencias_entre_datas(data_inicial, data_final)
    publicacoes_por_competencia = (
        tce_despesas_persistence.listar_publicacoes_competencias(
            codigo_municipio_tce=codigo_municipio,
            competencias=competencias,
        )
    )
    publicacoes = [
        {
            "codigo_municipio_tce": codigo_municipio,
            "competencia": competencia,
            "publicado_em": publicado_em,
        }
        for competencia, publicado_em in publicacoes_por_competencia.items()
    ]
    falhas = tce_despesas_persistence.listar_falhas_competencias(
        codigo_municipio_tce=codigo_municipio,
        competencias=competencias,
    )
    competencias_publicadas = [
        competencia for competencia in competencias
        if competencia in publicacoes_por_competencia
    ]

    registros = (
        tce_despesas_persistence.listar_empenhos_liquidos_publicados(
            codigo_municipio_tce=codigo_municipio,
            data_inicial=date.fromisoformat(data_inicial),
            data_final=date.fromisoformat(data_final),
        )
        if competencias_publicadas
        else []
    )
    registros = [
        registro
        for registro in registros
        if str(registro.get("data_empenho", ""))[:7] in competencias_publicadas
    ]
    base_tce = pd.DataFrame(registros)
    if not base_tce.empty:
        cnpjs = extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor"))
        caches = database.listar_fornecedores_cache(cnpjs) if cnpjs else {}
        if caches:
            fornecedores_cache = []
            for cnpj, cache in caches.items():
                dados = dict(cache.get("dados_normalizados") or {})
                dados.setdefault("cnpj", cnpj)
                fornecedores_cache.append(dados)
            base_tce = enriquecer_com_fornecedor(
                base_tce,
                pd.DataFrame(fornecedores_cache),
                coluna_cnpj="cnpj_fornecedor",
            )

    if municipio_comprador is None:
        municipio = database.localizar_municipio_tce(codigo_municipio)
        municipio_comprador = municipio["nome"] if municipio else None
    base_analitica = analitico.montar_base_analitica_tce(
        base_tce,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
        uf_comprador=UF_PADRAO,
    )
    qualidade = tce_observability.construir_metadados_carga(
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
        competencias_solicitadas=competencias,
        publicacoes=publicacoes,
        falhas=falhas,
        registros=registros,
        municipios_no_escopo=1,
    )
    metadados = {
        "fonte": "TCE-CE",
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "codigo_municipio": codigo_municipio,
        },
        "competencias_solicitadas": competencias,
        "competencias_publicadas": competencias_publicadas,
        "competencias_atualizadas": [],
        "competencias_reutilizadas": competencias_publicadas,
        "competencias_desatualizadas": [],
        "competencias_ausentes": [
            item for item in competencias if item not in competencias_publicadas
        ],
        "falhas_competencias": [
            {
                "competencia": item["competencia"],
                "usou_versao_publicada": item["competencia"] in competencias_publicadas,
            }
            for item in falhas
        ],
        "politica_atualizacao": {
            "modo": "SOMENTE_BANCO",
            "atualizar_competencias": False,
            "atualizar_fornecedores": False,
            "usar_publicacao_anterior_em_falha": True,
        },
        "totais": {"despesas": len(registros)},
        "base_analitica": {
            "nome": "despesas_tce",
            "base_calculo": "DESPESA_FINAL",
            "municipio_comprador": municipio_comprador,
            "colunas": list(analitico.COLUNAS_BASE_ANALITICA_TCE),
        },
        **qualidade,
    }
    return base_analitica, metadados


def montar_base_analitica_pagamentos_tce_somente_banco(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Base de pagamentos com empenhos usados apenas como dimensoes."""
    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
    competencias_pagamentos: list[str] = []
    registros, competencias_ausentes = tce_pagamentos_persistence.listar_pagamentos_municipais_para_analise(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=inicio,
        data_final=fim,
        competencias_publicadas=competencias_pagamentos,
    )
    if competencias_ausentes:
        raise CompetenciasTceIndisponiveisError(
            f"Lotes de pagamentos publicados ausentes: {competencias_ausentes[:10]} (total={len(competencias_ausentes)})."
        )
    base_tce = pd.DataFrame(registros)
    if not base_tce.empty:
        cnpjs = extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor"))
        caches = database.listar_fornecedores_cache(cnpjs) if cnpjs else {}
        if caches:
            fornecedores_cache = []
            for cnpj, cache in caches.items():
                dados = dict(cache.get("dados_normalizados") or {})
                dados.setdefault("cnpj", cnpj)
                fornecedores_cache.append(dados)
            base_tce = enriquecer_com_fornecedor(
                base_tce, pd.DataFrame(fornecedores_cache), coluna_cnpj="cnpj_fornecedor",
            )
    municipio = database.localizar_municipio_tce(codigo_municipio)
    base = analitico.montar_base_analitica_tce(
        base_tce,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio["nome"] if municipio else None,
        uf_comprador=UF_PADRAO,
    )
    if not base.empty:
        base["exercicio_orcamento"] = base_tce["exercicio_orcamento"]
        base["valor"] = base_tce["valor_centavos"].map(
            lambda valor: Decimal(int(valor)) / Decimal(100)
        )
        base["base_calculo"] = "PAGAMENTOS_TCE"
        base["valor_empenhado"] = pd.NA
        base["valor_anulado"] = pd.NA
    competencias_pagamentos = sorted(set(competencias_pagamentos))
    return base, {
        "fonte": "TCE-CE/SIM",
        "parametros": {"data_inicial": data_inicial, "data_final": data_final,
                       "codigo_municipio": codigo_municipio},
        "competencias_solicitadas": _competencias_entre_datas(data_inicial, data_final),
        "competencias_publicadas": competencias_pagamentos,
        "competencias_pagamentos_incluidas": competencias_pagamentos,
        "pagamentos_estendidos_automaticamente": bool(
            competencias_pagamentos
            and competencias_pagamentos[-1] > fim.strftime("%Y-%m")
        ),
        "competencias_ausentes": [],
        "totais": {"pagamentos": len(registros), "despesas": len(registros)},
        "data_available": bool(registros),
        "is_stale": False,
        "source_status": "CURRENT" if registros else "EMPTY",
        "quality": {"coverage": "COMPLETE", "missing_competencies": []},
        "base_analitica": {"nome": "pagamentos_tce", "base_calculo": "PAGAMENTOS",
                           "dimensoes_empenho": ["natureza", "fornecedor"]},
        "unidade_monetaria": "CENTAVOS",
    }


def montar_base_analitica_pagamentos_tce_estado_somente_banco(
    data_inicial: str, data_final: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    competencias_pagamentos: list[str] = []
    registros, ausentes = tce_pagamentos_persistence.listar_pagamentos_estaduais_para_analise(
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
        uf=UF_PADRAO,
        competencias_publicadas=competencias_pagamentos,
    )
    if ausentes:
        amostra = ausentes[:10]
        raise CompetenciasTceIndisponiveisError(
            f"Lotes de pagamentos ausentes ({len(ausentes)} municipio/competencia): {amostra}"
        )
    base_tce = pd.DataFrame(registros)
    if not base_tce.empty:
        cnpjs = extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor"))
        caches = database.listar_fornecedores_cache(cnpjs) if cnpjs else {}
        if caches:
            fornecedores_cache = []
            for cnpj, cache in caches.items():
                dados = dict(cache.get("dados_normalizados") or {})
                dados.setdefault("cnpj", cnpj)
                fornecedores_cache.append(dados)
            base_tce = enriquecer_com_fornecedor(
                base_tce, pd.DataFrame(fornecedores_cache), coluna_cnpj="cnpj_fornecedor",
            )
    base = analitico.montar_base_analitica_tce(base_tce, uf_comprador=UF_PADRAO)
    if not base.empty:
        base["exercicio_orcamento"] = base_tce["exercicio_orcamento"]
        base["valor"] = base_tce["valor_centavos"].map(
            lambda valor: Decimal(int(valor)) / Decimal(100)
        )
        base["base_calculo"] = "PAGAMENTOS_TCE"
        base["valor_empenhado"] = pd.NA
        base["valor_anulado"] = pd.NA
    return base, {
        "fonte": "TCE-CE/SIM",
        "parametros": {"data_inicial": data_inicial, "data_final": data_final},
        "competencias_solicitadas": _competencias_entre_datas(data_inicial, data_final),
        "competencias_publicadas": sorted(set(competencias_pagamentos)),
        "competencias_pagamentos_incluidas": sorted(set(competencias_pagamentos)),
        "pagamentos_estendidos_automaticamente": bool(
            competencias_pagamentos
            and max(competencias_pagamentos) > date.fromisoformat(data_final).strftime("%Y-%m")
        ),
        "competencias_ausentes": [],
        "totais": {"pagamentos": len(registros), "despesas": len(registros)},
        "data_available": bool(registros),
        "base_analitica": {"nome": "pagamentos_tce", "base_calculo": "PAGAMENTOS",
                           "dimensoes_empenho": ["natureza", "fornecedor"]},
        "unidade_monetaria": "CENTAVOS",
    }


def montar_indicadores_analiticos_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    municipio_comprador: str | None = None,
    throttle_fornecedores: float = 0.3,
    limite_ranking: int = 10,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    del throttle_fornecedores
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
    )
    indicadores = kpis.calcular_indicadores_analiticos_principais(
        base,
        limite_ranking=limite_ranking,
    )
    metadados = {
        **metadados,
        "indicadores_analiticos": {
            "escopo": "principais",
            "nomes": list(kpis.INDICADORES_ANALITICOS_PRINCIPAIS),
        },
    }
    return indicadores, metadados


def consultar_tce_indicadores_analiticos(
    *,
    limite: int | None = 100,
    limite_ranking: int = 10,
    **kwargs: Any,
) -> dict[str, Any]:
    indicadores, metadados = montar_indicadores_analiticos_tce(
        **kwargs,
        limite_ranking=limite_ranking,
    )
    resposta = {
        **metadados,
        "limite_resposta": limite,
        "kpi": "indicadores_analiticos_principais",
        "totais": _totais_tabelas(indicadores),
        "indicadores": tabelas_para_json(indicadores, limite=limite),
    }
    if not metadados["data_available"] or not metadados["totais"]["despesas"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def consultar_kpi_tce_portes_por_mes(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    throttle_fornecedores: float = 0.3,
    limite: int | None = 100,
) -> dict[str, Any]:
    del throttle_fornecedores
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
    )
    resultado = kpis.calcular_participacao_por_porte_por_mes(
        base,
        coluna_data="data_referencia",
        coluna_valor="valor",
        coluna_porte="porte_fornecedor",
    )
    resposta = {
        **metadados,
        "limite_resposta": limite,
        "totais": {
            "empenhos": int(len(base)),
            "registros_kpi": int(len(resultado)),
        },
        "kpi": "participacao_por_porte_por_mes",
        "dados": dataframe_para_registros(resultado, limite=limite),
    }
    if not metadados["data_available"] or not metadados["totais"]["despesas"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def consultar_overview_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str | None = None,
    throttle_fornecedores: float = 0.3,
) -> dict[str, Any]:
    del throttle_fornecedores
    if codigo_municipio is None:
        return _consultar_overview_tce_estado(data_inicial, data_final)
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
    )
    overview = kpis.calcular_overview_me_mei(base, incluir_mei=False)
    resposta = {
        **metadados,
        "kpi": "overview_me_mei",
        "escopo": {
            "portes_considerados": ["ME"],
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "ME / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME",
        },
        "unidade_monetaria": "CENTAVOS",
        **overview,
    }
    if not metadados["data_available"] or not metadados["totais"]["despesas"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


PORTES_FILTRO_OVERVIEW_MUNICIPAL = {
    "ME": "ME",
    "MEI": "MEI",
    "EPP": "EPP",
    "OTHER": "DEMAIS",
    "UNKNOWN": "NAO_IDENTIFICADO",
}


def consultar_overview_municipal_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str,
    company_sizes: list[str] | None = None,
    throttle_fornecedores: float = 0.3,
) -> dict[str, Any]:
    """Calcula o overview municipal para todos os portes ou para o recorte pedido."""
    del throttle_fornecedores
    filtros = list(dict.fromkeys(company_sizes or []))
    invalidos = sorted(set(filtros) - set(PORTES_FILTRO_OVERVIEW_MUNICIPAL))
    if invalidos:
        raise ValueError(f"Portes empresariais invalidos: {', '.join(invalidos)}.")
    portes = (
        [PORTES_FILTRO_OVERVIEW_MUNICIPAL[item] for item in filtros]
        if filtros
        else list(kpis.PORTES_EMPRESARIAIS)
    )
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
    )
    ano_exercicio = date.fromisoformat(data_inicial).year
    overview = kpis.calcular_overview_portes(
        base,
        portes_considerados=portes,
        ano_exercicio=ano_exercicio,
    )
    resposta = {
        **metadados,
        "kpi": "overview_municipal_por_portes",
        "escopo": {
            "portes_considerados": portes,
            "filtro_company_sizes": filtros,
            "regra_filtro": (
                "FILTRO_EXPLICITO" if filtros else "TODOS_OS_PORTES"
            ),
            "regra_geografica": "TODOS_OS_PORTES_CONSIDERADOS",
        },
        "unidade_monetaria": "CENTAVOS",
        **overview,
    }
    if not metadados["data_available"] or not metadados["totais"]["despesas"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def _consultar_overview_tce_estado(
    data_inicial: str,
    data_final: str,
) -> dict[str, Any]:
    """Calcula o overview estadual a partir de notas pagas publicadas."""
    base, metadados = montar_base_analitica_pagamentos_tce_estado_somente_banco(
        data_inicial, data_final,
    )
    ano_exercicio = date.fromisoformat(data_inicial).year
    overview = kpis.calcular_overview_me_mei(
        base, incluir_mei=False, ano_exercicio=ano_exercicio,
    )
    return {
        **metadados,
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "parametros": {"data_inicial": data_inicial, "data_final": data_final,
                       "codigo_municipio": None, "uf": UF_PADRAO},
        "escopo_territorial": {
            "tipo": "ESTADO", "uf": UF_PADRAO, "nome": "Ceara",
            "quantidade_municipios_com_publicacao": len(database.listar_municipios_ibge(UF_PADRAO)),
        },
        "kpi": "overview_me",
        "unidade_monetaria": "CENTAVOS",
        "escopo": {
            "portes_considerados": ["ME"],
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "ME / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME",
        },
        **overview,
    }
