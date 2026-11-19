from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Any
import pandas as pd
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analitico, kpis
from app.pipeline import tce_observability
from app.pipeline.enrichment.fornecedores import (
    enriquecer_com_fornecedor,
)
from app.pipeline.persistence import tce_analytics

class CompetenciasTceIndisponiveisError(RuntimeError):
    """Nenhuma competência solicitada possui uma publicação utilizável."""


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


def _fim_pagamentos_exercicio(data_inicial: str, data_final: str) -> str:
    """Inclui pagamentos posteriores vinculados ao orçamento do exercício."""
    inicio = date.fromisoformat(data_inicial)
    fim = date.fromisoformat(data_final)
    hoje = datetime.now(ZoneInfo("America/Fortaleza")).date()
    if fim == date(inicio.year, 12, 31) and inicio.year < hoje.year:
        return max(fim, hoje).isoformat()
    return data_final


def _enriquecer_registros_fornecedores(
    registros: list[dict[str, Any]],
    caches: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Transforma registros e snapshots recebidos, sem executar consultas."""
    base = pd.DataFrame(registros)
    if base.empty or not caches:
        return base
    fornecedores = []
    for cnpj, cache in caches.items():
        dados = dict(cache.get("dados_normalizados") or {})
        dados.setdefault("cnpj", cnpj)
        fornecedores.append(dados)
    if not fornecedores:
        return base
    return enriquecer_com_fornecedor(
        base,
        pd.DataFrame(fornecedores),
        coluna_cnpj="cnpj_fornecedor",
    )


def montar_base_analitica_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    municipio_comprador: str | None = None,
    throttle_fornecedores: float = 0.3,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compatibilidade: a montagem agora e invariavelmente somente banco."""
    del throttle_fornecedores
    base, metadados = montar_base_analitica_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        municipio_comprador=municipio_comprador,
    )
    if not metadados["competencias_publicadas"]:
        competencias = ", ".join(metadados["competencias_solicitadas"])
        raise CompetenciasTceIndisponiveisError(
            "Nenhuma competencia do TCE-CE possui publicacao utilizavel: "
            f"{competencias}."
        )
    return base, metadados


def montar_base_analitica_tce_somente_banco(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str,
    municipio_comprador: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Calcula a base analítica sem atualizar lotes ou consultar fontes externas."""
    competencias = _competencias_entre_datas(data_inicial, data_final)
    snapshot = tce_analytics.carregar_snapshot_despesas(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
        competencias=competencias,
    )
    publicacoes = [
        {
            "codigo_municipio_tce": codigo_municipio,
            "competencia": competencia,
            "publicado_em": publicado_em,
        }
        for competencia, publicado_em in snapshot.publicacoes_por_competencia.items()
    ]
    competencias_publicadas = [
        competencia for competencia in competencias
        if competencia in snapshot.publicacoes_por_competencia
    ]
    registros = snapshot.registros
    base_tce = _enriquecer_registros_fornecedores(
        registros,
        snapshot.fornecedores_cache,
    )
    municipio_comprador = municipio_comprador or snapshot.municipio_comprador
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
        falhas=snapshot.falhas,
        registros=registros,
        municipios_no_escopo=1,
        raizes_com_porte_historico=snapshot.raizes_com_porte_historico,
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
                "erro": item.get("erro"),
                "usou_versao_publicada": item["competencia"] in competencias_publicadas,
            }
            for item in snapshot.falhas
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
    municipio_comprador: str | None = None,
    exercicio_orcamento: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Base de pagamentos com empenhos usados apenas como dimensoes."""
    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
    snapshot = tce_analytics.carregar_snapshot_pagamentos_municipais(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=inicio,
        data_final=fim,
        exercicio_orcamento=exercicio_orcamento,
    )
    registros = [
        registro
        for registro in snapshot.registros
        if str(registro.get("tipo_documento_fornecedor") or "").strip() == "1"
        and registro.get("cnpj_fornecedor")
    ]
    base_tce = _enriquecer_registros_fornecedores(
        registros,
        snapshot.fornecedores_cache,
    )
    nome_municipio = municipio_comprador or snapshot.municipio_comprador
    base = analitico.montar_base_analitica_tce(
        base_tce,
        codigo_municipio=codigo_municipio,
        municipio_comprador=nome_municipio,
        uf_comprador=UF_PADRAO,
    )
    if not base.empty:
        if "exercicio_orcamento" in base_tce.columns:
            base["exercicio_orcamento"] = base_tce["exercicio_orcamento"]
        else:
            base["exercicio_orcamento"] = date.fromisoformat(data_inicial).year * 100
        base["valor"] = base_tce["valor_centavos"].map(
            lambda valor: Decimal(int(valor)) / Decimal(100)
        )
        base["base_calculo"] = "PAGAMENTOS_TCE"
        base["valor_empenhado"] = pd.NA
        base["valor_anulado"] = pd.NA
    competencias_pagamentos = snapshot.competencias_publicadas
    return base, {
        "fonte": "TCE-CE/SIM",
        "parametros": {"data_inicial": data_inicial, "data_final": data_final,
                       "codigo_municipio": codigo_municipio,
                       "exercicio_orcamentario": exercicio_orcamento},
        "competencias_solicitadas": _competencias_entre_datas(data_inicial, data_final),
        "competencias_com_dados": competencias_pagamentos,
        "competencias_publicadas": competencias_pagamentos,
        "competencias_pagamentos_incluidas": competencias_pagamentos,
        "pagamentos_estendidos_automaticamente": False,
        "competencias_ausentes": [
            item
            for item in snapshot.competencias_ausentes
        ],
        "totais": {"pagamentos": len(registros)},
        "data_available": bool(registros),
        "last_successful_load_at": tce_observability.ultima_atualizacao_concluida(
            snapshot.publicacoes_concluidas_em
        ),
        "is_stale": False,
        "source_status": "CURRENT" if registros else "EMPTY",
        "quality": {
            "coverage": "PARTIAL" if snapshot.competencias_ausentes else "COMPLETE",
            "missing_competencies": [
                item
                for item in snapshot.competencias_ausentes
            ],
        },
        "base_analitica": {"nome": "pagamentos_tce", "base_calculo": "PAGAMENTOS",
                           "dimensoes_empenho": ["natureza", "fornecedor"],
                           "escopo_fornecedor": "CNPJ_VALIDO"},
        "unidade_monetaria": "CENTAVOS",
    }


def montar_base_analitica_pagamentos_tce_estado_somente_banco(
    data_inicial: str,
    data_final: str,
    *,
    exercicio_orcamento: int | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    snapshot = tce_analytics.carregar_snapshot_pagamentos_estaduais(
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
        exercicio_orcamento=exercicio_orcamento,
    )
    registros = [
        registro
        for registro in snapshot.registros
        if str(registro.get("tipo_documento_fornecedor") or "").strip() == "1"
        and registro.get("cnpj_fornecedor")
    ]
    competencias_pagamentos = snapshot.competencias_publicadas
    base_tce = _enriquecer_registros_fornecedores(
        registros,
        snapshot.fornecedores_cache,
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
        "parametros": {
            "data_inicial": data_inicial,
            "data_final": data_final,
            "exercicio_orcamentario": exercicio_orcamento,
        },
        "competencias_solicitadas": _competencias_entre_datas(data_inicial, data_final),
        "competencias_com_dados": sorted(set(competencias_pagamentos)),
        "competencias_publicadas": sorted(set(competencias_pagamentos)),
        "competencias_pagamentos_incluidas": sorted(set(competencias_pagamentos)),
        "pagamentos_estendidos_automaticamente": bool(
            competencias_pagamentos
            and max(competencias_pagamentos) > date.fromisoformat(data_final).strftime("%Y-%m")
        ),
        "competencias_ausentes": [
            item.get("competencia") if isinstance(item, dict) else item
            for item in snapshot.competencias_ausentes
        ],
        "totais": {"pagamentos": len(registros)},
        "data_available": bool(registros),
        "last_successful_load_at": tce_observability.ultima_atualizacao_concluida(
            snapshot.publicacoes_concluidas_em
        ),
        "base_analitica": {"nome": "pagamentos_tce", "base_calculo": "PAGAMENTOS",
                           "dimensoes_empenho": ["natureza", "fornecedor"]},
        "quality": {
            "coverage": "PARTIAL" if snapshot.competencias_ausentes else "COMPLETE",
            "missing_competencies": [
                item.get("competencia") if isinstance(item, dict) else item
                for item in snapshot.competencias_ausentes
            ],
        },
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
    if not metadados["data_available"] or not metadados["totais"]["pagamentos"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def consultar_kpi_tce_portes_por_mes(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str = CODIGO_MUNICIPIO_TCE_PADRAO,
    exercicio_orcamentario: int | None = None,
    throttle_fornecedores: float = 0.3,
    limite: int | None = 100,
) -> dict[str, Any]:
    del throttle_fornecedores
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        exercicio_orcamento=exercicio_orcamentario,
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
            "pagamentos": int(len(base)),
            "registros_kpi": int(len(resultado)),
        },
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "kpi": "participacao_por_porte_por_mes",
        "dados": dataframe_para_registros(resultado, limite=limite),
    }
    if not metadados["data_available"] or not metadados["totais"]["pagamentos"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def consultar_overview_pagamentos_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str,
    exercicio_orcamentario: int,
    incluir_resumo_compras: bool = False,
) -> dict[str, Any]:
    """Consolida pagamentos locais sem confundir ME com o KPI agregado de MPE."""
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        exercicio_orcamento=exercicio_orcamentario,
    )
    overview = kpis.calcular_overview_portes(
        base,
        portes_considerados=list(kpis.PORTES_EMPRESARIAIS),
        ano_exercicio=exercicio_orcamentario,
    )
    if base.empty:
        valores_centavos = pd.Series(dtype="int64")
        por_porte: dict[str, int] = {}
        me_por_elemento = {
            codigo: 0 for codigo in kpis.NATUREZAS_DESPESA_MONITORADAS
        }
        total_me = total_mpe_identificado = total = 0
        classificacao_mpe_completa = True
    else:
        valores_centavos = base["valor"].map(kpis._valor_reais_para_centavos)
        total = int(valores_centavos.sum())
        por_porte = {
            str(porte): int(valores_centavos.loc[base["porte_fornecedor"].eq(porte)].sum())
            for porte in kpis.PORTES_EMPRESARIAIS
        }
        mascara_me = base["porte_fornecedor"].eq("ME")
        total_me = int(valores_centavos.loc[mascara_me].sum())
        me_por_elemento = {
            codigo: int(valores_centavos.loc[
                mascara_me & base["natureza_despesa_codigo"].eq(codigo)
            ].sum())
            for codigo in kpis.NATUREZAS_DESPESA_MONITORADAS
        }
        marcador_mpe = base["fornecedor_e_mpe"].astype("boolean")
        classificacao_mpe_completa = not bool(marcador_mpe.isna().any())
        total_mpe_identificado = int(
            valores_centavos.loc[marcador_mpe.fillna(False)].sum()
        )
    cobertura_centavos = sum(
        valor for porte, valor in por_porte.items()
        if porte != analitico.PORTE_NAO_IDENTIFICADO
    )
    controle_financeiro = {
        "total_centavos": total,
        "por_elemento_centavos": {
            codigo: int(valores_centavos.loc[
                base["natureza_despesa_codigo"].eq(codigo)
            ].sum()) if not base.empty else 0
            for codigo in kpis.NATUREZAS_DESPESA_MONITORADAS
        },
        "por_porte_centavos": por_porte,
        "reconciliado": True,
    }
    if incluir_resumo_compras:
        overview["resumo_compras"] = _resumir_compras_municipais(base, overview)
    return {
        **metadados,
        "kpi": "overview_pagamentos_me_mpe",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "unidade_monetaria": "CENTAVOS",
        "escopo_financeiro": {
            "fornecedor": "CNPJ_VALIDO",
            "elementos": list(kpis.NATUREZAS_DESPESA_MONITORADAS),
            "camara_excluida_por": "CODIGO_TIPO_UNIDADE_02",
        },
        "criterio_me": {
            "porte": "ME",
            "observacao": "Subtotal de ME; nao deve ser rotulado como MPE.",
        },
        "criterio_mpe": {
            "regra": "ME + EPP + MEI quando indicado pelo cadastro",
            "classificacao_completa": classificacao_mpe_completa,
        },
        "total_me_centavos": total_me,
        "me_por_elemento_centavos": me_por_elemento,
        "total_mpe_centavos": (
            total_mpe_identificado if classificacao_mpe_completa else None
        ),
        "total_mpe_identificado_centavos": total_mpe_identificado,
        "cobertura_cadastral": {
            "valor_classificado_centavos": cobertura_centavos,
            "valor_total_centavos": total,
            "percentual": (
                float(Decimal(cobertura_centavos) * Decimal(100) / Decimal(total))
                if total else 100.0
            ),
        },
        "controle_financeiro": controle_financeiro,
        **overview,
    }


def consultar_overview_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str | None = None,
    throttle_fornecedores: float = 0.3,
) -> dict[str, Any]:
    del throttle_fornecedores
    data_final = _fim_pagamentos_exercicio(data_inicial, data_final)
    if codigo_municipio is None:
        return _consultar_overview_tce_estado(data_inicial, data_final)
    base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        data_inicial,
        data_final,
        codigo_municipio=codigo_municipio,
        exercicio_orcamento=date.fromisoformat(data_inicial).year,
    )
    overview = kpis.calcular_overview_me_mei(base, incluir_mei=False)
    resposta = {
        **metadados,
        "kpi": "overview_me_mei",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
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
    if not metadados["data_available"] or not metadados["totais"]["pagamentos"]:
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
    incluir_resumo_compras: bool = False,
) -> dict[str, Any]:
    """Calcula o overview municipal para todos os portes ou para o recorte pedido."""
    del throttle_fornecedores
    data_final = _fim_pagamentos_exercicio(data_inicial, data_final)
    filtros = list(dict.fromkeys(company_sizes or []))
    invalidos = sorted(set(filtros) - set(PORTES_FILTRO_OVERVIEW_MUNICIPAL))
    if invalidos:
        raise ValueError(f"Portes empresariais invalidos: {', '.join(invalidos)}.")
    portes = (
        [PORTES_FILTRO_OVERVIEW_MUNICIPAL[item] for item in filtros]
        if filtros
        else list(kpis.PORTES_EMPRESARIAIS)
    )
    ano_exercicio = date.fromisoformat(data_inicial).year
    base, metadados = (
        montar_base_analitica_pagamentos_tce_somente_banco(
            data_inicial,
            data_final,
            codigo_municipio=codigo_municipio,
            exercicio_orcamento=ano_exercicio,
        )
    )
    overview = kpis.calcular_overview_portes(
        base,
        portes_considerados=portes,
    )
    if incluir_resumo_compras:
        raise ValueError(
            "O resumo de compras ja integra o overview baseado em pagamentos."
        )
    resposta = {
        **metadados,
        "kpi": "overview_municipal_por_portes",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
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
    if not metadados["data_available"] or not metadados["totais"]["pagamentos"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta


def _resumir_compras_municipais(
    base: pd.DataFrame,
    overview: dict[str, Any],
    *,
    limite_fornecedores: int = 10,
) -> dict[str, Any]:
    """Relaciona valores pagos, fornecedores e compras atribuiveis a origem local."""
    if base.empty:
        total_centavos = 0
        total_compras = 0
        compras_locais = 0
        ranking: list[dict[str, Any]] = []
        fornecedores_identificados = 0
        fornecedores_sem_identificacao = 0
    else:
        dados = base.copy()
        dados["_valor_centavos"] = dados["valor"].map(kpis._valor_reais_para_centavos)
        chaves_compras = dados["chave_empenho"].dropna().astype(str)
        total_compras = int(chaves_compras.nunique())
        locais = dados.loc[
            dados["origem_geografica"].eq(kpis.ORIGEM_FORNECEDOR_LOCAL)
        ]
        compras_locais = int(locais["chave_empenho"].dropna().astype(str).nunique())

        identificadores = dados["documento_fornecedor"].astype("string").str.strip()
        tipos_documento = dados["tipo_documento_fornecedor"].astype("string").str.strip()
        nomes = dados["nome_fornecedor"].astype("string").str.strip()
        dados["_documento"] = identificadores.fillna("")
        dados["_tipo_documento"] = tipos_documento.fillna("")
        dados["_nome"] = nomes.fillna("").replace("", "Fornecedor não identificado")
        dados["_chave_fornecedor"] = dados.apply(
            lambda linha: (
                f"{linha['_tipo_documento']}:{linha['_documento']}"
                if linha["_documento"]
                else f"NOME:{linha['_nome'].casefold()}"
            ),
            axis=1,
        )
        agrupados = (
            dados.groupby("_chave_fornecedor", dropna=False)
            .agg(
                nome=("_nome", "first"),
                documento=("_documento", "first"),
                tipo_documento=("_tipo_documento", "first"),
                municipio_sede=("municipio_sede_fornecedor", "first"),
                uf_sede=("uf_sede_fornecedor", "first"),
                origem=("origem_geografica", "first"),
                valor_centavos=("_valor_centavos", "sum"),
                quantidade_compras=("chave_empenho", "nunique"),
            )
            .sort_values(["valor_centavos", "quantidade_compras"], ascending=False)
        )
        ranking = [
            {
                "nome": str(linha.nome),
                "documento": str(linha.documento) or None,
                "tipo_documento": str(linha.tipo_documento) or None,
                "municipio_sede": str(linha.municipio_sede) if pd.notna(linha.municipio_sede) else None,
                "uf_sede": str(linha.uf_sede) if pd.notna(linha.uf_sede) else None,
                "origem": str(linha.origem),
                "valor_centavos": int(linha.valor_centavos),
                "quantidade_compras": int(linha.quantidade_compras),
            }
            for linha in agrupados.head(limite_fornecedores).itertuples(index=False)
        ]
        fornecedores_identificados = int(
            dados.loc[dados["_documento"].ne(""), "_chave_fornecedor"].nunique()
        )
        fornecedores_sem_identificacao = int(
            dados.loc[dados["_documento"].eq(""), "_chave_fornecedor"].nunique()
        )
        total_centavos = int(overview["kpis"]["total_compras_centavos"])

    destinos = {item["destino"]: item for item in overview.get("destino_recursos", [])}
    destino_local = destinos.get("NO_MUNICIPIO_COMPRADOR", {})
    valor_local = int(destino_local.get("valor_centavos", 0))
    return {
        "medida": "PAGAMENTOS_PUBLICADOS_ASSOCIADOS_A_EMPENHOS",
        "regra_contagem_compras": "EMPENHOS_UNICOS_COM_PAGAMENTO_NO_PERIODO",
        "unidade_monetaria": "CENTAVOS",
        "valor_total_centavos": total_centavos,
        "quantidade_compras": total_compras,
        "quantidade_fornecedores_identificados": fornecedores_identificados,
        "quantidade_fornecedores_sem_documento": fornecedores_sem_identificacao,
        "compras_na_cidade": {
            "quantidade_compras": compras_locais,
            "valor_centavos": valor_local,
            "percentual_do_valor_total": float(
                destino_local.get("percentual", 0.0)
            ),
        },
        "fornecedores": ranking,
        "fornecedores_exibidos": len(ranking),
        "limite_fornecedores": limite_fornecedores,
    }

def consultar_participacao_me_por_natureza_tce(
    data_inicial: str,
    data_final: str,
    *,
    codigo_municipio: str | None = None,
    company_sizes: list[str] | None = None,
    throttle_fornecedores: float = 0.3,
) -> dict[str, Any]:
    """Prepara o recorte analítico e agrega a participação de ME por natureza."""
    filtros = list(dict.fromkeys(company_sizes or []))
    invalidos = sorted(set(filtros) - set(PORTES_FILTRO_OVERVIEW_MUNICIPAL))
    if invalidos:
        raise ValueError(f"Portes empresariais invalidos: {', '.join(invalidos)}.")
    portes = (
        [
            PORTES_FILTRO_OVERVIEW_MUNICIPAL[item]
            for item in filtros
            if PORTES_FILTRO_OVERVIEW_MUNICIPAL[item]
            in kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
        ]
        if filtros
        else list(kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW)
    )
    exercicio = date.fromisoformat(data_inicial).year
    if codigo_municipio is None:
        base, metadados = montar_base_analitica_pagamentos_tce_estado_somente_banco(
            data_inicial,
            data_final,
            exercicio_orcamento=exercicio,
        )
    else:
        base, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
            data_inicial,
            data_final,
            codigo_municipio=codigo_municipio,
            exercicio_orcamento=exercicio,
        )
    return {
        **metadados,
        "kpi": "participacao_me_por_natureza_despesa",
        "escopo": {
            "portes_considerados": portes,
            "porte_numerador": "ME",
            "regra_denominador": "ME + MEI + EPP + DEMAIS dentre os portes considerados",
            "filtro_company_sizes": filtros,
            "regra_natureza": "código de dois dígitos e descrição padronizada",
        },
        "unidade_monetaria": "CENTAVOS",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "participacao_me_por_natureza": kpis.calcular_participacao_me_por_natureza(
            base, portes_considerados=portes
        ),
    }


def _consultar_overview_tce_estado(
    data_inicial: str,
    data_final: str,
) -> dict[str, Any]:
    """Consolida pagamentos publicados de todo o Ceara."""
    data_final = _fim_pagamentos_exercicio(data_inicial, data_final)
    exercicio = date.fromisoformat(data_inicial).year
    base, metadados = montar_base_analitica_pagamentos_tce_estado_somente_banco(
        data_inicial,
        data_final,
        exercicio_orcamento=exercicio,
    )
    overview = kpis.calcular_overview_me_mei(base, incluir_mei=False)
    ranking = kpis.calcular_ranking_municipios_overview_estadual(
        base,
        total_me_centavos=int(overview["kpis"]["total_compras_ME_centavos"]),
    )
    municipios = []
    if not base.empty:
        municipios = [
            {"codigo_tce": str(codigo), "nome": str(nome)}
            for codigo, nome in base[["codigo_municipio_tce", "municipio_comprador"]]
            .drop_duplicates()
            .itertuples(index=False, name=None)
        ]
    resposta = {
        "fonte": "TCE-CE",
        **metadados,
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "parametros": {
            **dict(metadados.get("parametros") or {}),
            "codigo_municipio": None,
            "uf": UF_PADRAO,
            "exercicio_orcamentario": exercicio,
        },
        "escopo_territorial": {
            "tipo": "ESTADO",
            "uf": UF_PADRAO,
            "nome": "Ceara",
            "quantidade_municipios_com_pagamentos": len(municipios),
            "municipios_com_pagamentos": sorted(municipios, key=lambda item: item["nome"]),
        },
        "totais_brutos": {"pagamentos": int(len(base))},
        "base_analitica": {
            "nome": "tce_pagamentos",
            "base_calculo": "PAGAMENTOS",
            "dimensoes_empenho": ["natureza", "fornecedor"],
        },
        "kpi": "overview_me",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "unidade_monetaria": "CENTAVOS",
        "escopo": {
            "portes_considerados": ["ME"],
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "ME / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME",
        },
        "ranking_municipios_me": ranking,
        **overview,
    }
    if not metadados["data_available"] or not metadados["totais"]["pagamentos"]:
        resposta = tce_observability.tornar_percentuais_indisponiveis(resposta)
    return resposta
