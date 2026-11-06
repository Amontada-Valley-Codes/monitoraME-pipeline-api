from datetime import date
from decimal import Decimal
from typing import Any
import pandas as pd
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline import analitico, kpis
from app.pipeline import tce_observability
from app.pipeline.enrichment.fornecedores import enriquecer_com_fornecedor
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
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Base de pagamentos com empenhos usados apenas como dimensoes."""
    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
    snapshot = tce_analytics.carregar_snapshot_pagamentos_municipais(
        codigo_municipio_tce=codigo_municipio,
        data_inicial=inicio,
        data_final=fim,
    )
    if snapshot.competencias_ausentes:
        raise CompetenciasTceIndisponiveisError(
            "Lotes de pagamentos publicados ausentes: "
            f"{snapshot.competencias_ausentes[:10]} "
            f"(total={len(snapshot.competencias_ausentes)})."
        )
    registros = snapshot.registros
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
    snapshot = tce_analytics.carregar_snapshot_pagamentos_estaduais(
        data_inicial=date.fromisoformat(data_inicial),
        data_final=date.fromisoformat(data_final),
    )
    if snapshot.competencias_ausentes:
        amostra = snapshot.competencias_ausentes[:10]
        raise CompetenciasTceIndisponiveisError(
            "Lotes de pagamentos ausentes "
            f"({len(snapshot.competencias_ausentes)} municipio/competencia): {amostra}"
        )
    registros = snapshot.registros
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
    if codigo_municipio is None:
        # O recorte estadual é estritamente local e reutiliza as mesmas partições
        # publicadas e os mesmos enriquecimentos do overview estadual.
        competencias = _competencias_entre_datas(data_inicial, data_final)
        publicacoes = tce_despesas_persistence.listar_publicacoes_competencias_estado(
            competencias=competencias,
            uf=UF_PADRAO,
        )
        if not publicacoes:
            raise CompetenciasTceIndisponiveisError(
                "Nenhuma competencia publicada para o Ceara no periodo solicitado."
            )
        empenhos = tce_despesas_persistence.listar_empenhos_liquidos_publicados(
            codigo_municipio_tce=None,
            data_inicial=date.fromisoformat(data_inicial),
            data_final=date.fromisoformat(data_final),
            uf=UF_PADRAO,
        )
        base_tce = pd.DataFrame(empenhos)
        cnpjs = (
            extrair_cnpjs_distintos(base_tce.get("cnpj_fornecedor"))
            if not base_tce.empty
            else []
        )
        caches = database.listar_fornecedores_cache(cnpjs) if cnpjs else {}
        fornecedores_cache = []
        for cnpj, cache in caches.items():
            dados = dict(cache.get("dados_normalizados") or {})
            dados.setdefault("cnpj", cnpj)
            fornecedores_cache.append(dados)
        if not base_tce.empty and fornecedores_cache:
            base_tce = enriquecer_com_fornecedor(
                base_tce, pd.DataFrame(fornecedores_cache), coluna_cnpj="cnpj_fornecedor"
            )
        base = analitico.montar_base_analitica_tce(base_tce, uf_comprador=UF_PADRAO)
        metadados: dict[str, Any] = {
            "fonte": "TCE-CE",
            "origem_dados": "BANCO_LOCAL",
            "consultas_externas": False,
            "parametros": {
                "data_inicial": data_inicial,
                "data_final": data_final,
                "codigo_municipio": None,
                "uf": UF_PADRAO,
            },
            "competencias_solicitadas": competencias,
            "competencias_publicadas": sorted(
                {str(item["competencia"]) for item in publicacoes}
            ),
            "totais_brutos": {"empenhos": len(empenhos)},
        }
    else:
        base, metadados = montar_base_analitica_tce(
            data_inicial,
            data_final,
            codigo_municipio=codigo_municipio,
            throttle_fornecedores=throttle_fornecedores,
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
        "participacao_me_por_natureza": kpis.calcular_participacao_me_por_natureza(
            base, portes_considerados=portes
        ),
    }


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
    base_estadual = base.loc[base["porte_fornecedor"].eq("ME")].copy()
    if "exercicio_orcamentario" in base_estadual.columns:
        base_estadual = base_estadual.loc[
            base_estadual["exercicio_orcamentario"].eq(ano_exercicio * 100)
        ]
    ufs_fornecedores = (
        base_estadual["uf_sede_fornecedor"].astype("string").str.strip().str.upper()
        if "uf_sede_fornecedor" in base_estadual.columns
        else pd.Series(pd.NA, index=base_estadual.index, dtype="string")
    )
    valores_centavos = base_estadual["valor"].map(kpis._valor_reais_para_centavos)
    valores_centavos = valores_centavos.fillna(0).astype("int64")
    valor_no_ceara = int(valores_centavos.loc[ufs_fornecedores.eq("CE").fillna(False)].sum())
    valor_fora_ceara = int(
        valores_centavos.loc[ufs_fornecedores.notna() & ufs_fornecedores.ne("") & ufs_fornecedores.ne("CE")].sum()
    )
    valor_nao_identificado = int(
        valores_centavos.loc[ufs_fornecedores.isna() | ufs_fornecedores.eq("")].sum()
    )
    total_me_centavos = int(overview["kpis"]["total_compras_ME_centavos"])
    overview["destino_recursos"] = [
        {
            "destino": destino,
            "valor_centavos": valor_centavos,
            "percentual": (
                float(Decimal(valor_centavos) * Decimal(100) / Decimal(total_me_centavos))
                if total_me_centavos
                else 0.0
            ),
        }
        for destino, valor_centavos in (
            ("NO_CEARA", valor_no_ceara),
            ("FORA_DO_CEARA", valor_fora_ceara),
            ("NAO_IDENTIFICADA", valor_nao_identificado),
        )
    ]
    return {
        **metadados,
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "parametros": {"data_inicial": data_inicial, "data_final": data_final,
                       "codigo_municipio": None, "uf": UF_PADRAO},
        "escopo_territorial": {
            "tipo": "ESTADO", "uf": UF_PADRAO, "nome": "Ceara",
            "quantidade_municipios_com_publicacao": (
                tce_analytics.contar_municipios_catalogados(UF_PADRAO)
            ),
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
