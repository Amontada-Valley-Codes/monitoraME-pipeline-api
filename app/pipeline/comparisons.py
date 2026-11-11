from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.core import database
from app.core.config import NATUREZAS_DESPESA_MONITORADAS, UF_PADRAO
from app.pipeline import kpis, tce_observability


_PORTES_FILTRO = {
    "ME": "ME",
    "MEI": "MEI",
    "EPP": "EPP",
    "OTHER": "DEMAIS",
    "UNKNOWN": "NAO_IDENTIFICADO",
}
_ORIGENS_FILTRO = dict(kpis.DESTINOS_RECURSOS_OVERVIEW)


class ComparisonRequestError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, Any]],
        *,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


def _data_iso(valor: str, *, campo: str) -> date:
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError) as error:
        raise ComparisonRequestError(
            "INVALID_DATE",
            f"{campo.split('.')[-1]} deve usar o formato YYYY-MM-DD.",
            [{"field": campo, "reason": "invalid_date", "value": valor}],
        ) from error


def _validar_datas(filtros: dict[str, Any], *, lado: str) -> tuple[date, date]:
    inicio = _data_iso(filtros["start_date"], campo=f"{lado}.filters.start_date")
    fim = _data_iso(filtros["end_date"], campo=f"{lado}.filters.end_date")
    if inicio > fim:
        raise ComparisonRequestError(
            "INVALID_DATE_RANGE",
            "start_date deve ser menor ou igual a end_date.",
            [
                {
                    "field": f"{lado}.filters.start_date",
                    "reason": "after_end_date",
                    "value": filtros["start_date"],
                }
            ],
        )
    return inicio, fim


def _validar_filtros(filtros: dict[str, Any], *, lado: str) -> tuple[date, date]:
    inicio, fim = _validar_datas(filtros, lado=lado)
    uf = str(filtros["uf"]).strip().upper()
    if uf != UF_PADRAO.upper():
        raise ComparisonRequestError(
            "UNSUPPORTED_UF",
            f"A comparacao possui dados apenas para a UF {UF_PADRAO.upper()}.",
            [
                {
                    "field": f"{lado}.filters.uf",
                    "reason": "unsupported_uf",
                    "value": filtros["uf"],
                }
            ],
        )

    codigos_invalidos = sorted(
        {
            str(codigo)
            for codigo in filtros.get("expense_element_codes", [])
            if str(codigo) not in NATUREZAS_DESPESA_MONITORADAS
        }
    )
    if codigos_invalidos:
        raise ComparisonRequestError(
            "INVALID_EXPENSE_ELEMENT_CODE",
            "expense_element_codes possui codigos nao monitorados.",
            [
                {
                    "field": f"{lado}.filters.expense_element_codes",
                    "reason": "unsupported_value",
                    "value": codigos_invalidos,
                }
            ],
        )
    return inicio, fim


def _aplicar_filtros(base: pd.DataFrame, filtros: dict[str, Any]) -> pd.DataFrame:
    resultado = base.copy()
    portes = filtros.get("company_sizes") or []
    if portes:
        internos = {_PORTES_FILTRO[str(porte)] for porte in portes}
        resultado = resultado.loc[resultado["porte_fornecedor"].isin(internos)]

    origens = filtros.get("supplier_origins") or []
    if origens:
        internas = {_ORIGENS_FILTRO[str(origem)] for origem in origens}
        resultado = resultado.loc[resultado["origem_geografica"].isin(internas)]

    elementos = filtros.get("expense_element_codes") or []
    if elementos:
        resultado = resultado.loc[
            resultado["natureza_despesa_codigo"].astype("string").isin(elementos)
        ]
    return resultado.reset_index(drop=True)


def _status_exercicio(inicio: date, fim: date) -> str:
    return "FECHADO" if fim.year < date.today().year else "EM_ANDAMENTO"


def _montar_overview_local(
    filtros: dict[str, Any],
    *,
    lado: str,
) -> dict[str, Any]:
    inicio, fim = _validar_filtros(filtros, lado=lado)
    uf = str(filtros["uf"]).strip().upper()
    codigo_ibge = str(filtros["municipality_ibge_code"]).strip()
    municipio = database.localizar_municipio_tce_por_ibge(codigo_ibge, uf=uf)
    if municipio is None:
        raise ComparisonRequestError(
            "MUNICIPALITY_NOT_FOUND",
            "Municipio nao encontrado no catalogo local.",
            [{
                "field": f"{lado}.filters.municipality_ibge_code",
                "reason": "not_found",
                "value": codigo_ibge,
            }],
            status_code=404,
        )

    from app.pipeline.analisys import montar_base_analitica_pagamentos_tce_somente_banco

    codigo_tce = municipio["codigo_municipio_tce"]
    base_analitica, metadados = montar_base_analitica_pagamentos_tce_somente_banco(
        inicio.isoformat(),
        fim.isoformat(),
        codigo_municipio=codigo_tce,
        exercicio_orcamento=inicio.year,
    )
    base_filtrada = _aplicar_filtros(base_analitica, filtros)
    overview = kpis.calcular_overview_me_mei(base_filtrada)
    pagamentos_com_fornecedor = int(len(base_analitica))
    fornecedores_com_porte = (
        int(base_analitica["porte_fornecedor"].ne("NAO_IDENTIFICADO").sum())
        if pagamentos_com_fornecedor
        else 0
    )
    pagamento_metadados = {
        "fonte": "TCE-CE/SIM",
        **metadados,
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "status_exercicio": _status_exercicio(inicio, fim),
        "parametros": {
            **dict(metadados.get("parametros") or {}),
            "data_inicial": filtros["start_date"],
            "data_final": filtros["end_date"],
            "uf": uf,
            "codigo_municipio_ibge": codigo_ibge,
            "codigo_municipio_tce": codigo_tce,
            "company_sizes": list(filtros.get("company_sizes") or []),
            "supplier_origins": list(filtros.get("supplier_origins") or []),
            "expense_element_codes": list(filtros.get("expense_element_codes") or []),
        },
        "municipio": {
            "nome": municipio["nome"],
            "uf": municipio["uf"],
            "codigo_ibge": municipio["codigo_municipio_ibge"],
            "codigo_tce": codigo_tce,
        },
        "totais_brutos": {"pagamentos": int(len(base_analitica))},
        "totais_filtrados": {"pagamentos": int(len(base_filtrada))},
        "cobertura_cadastral": {
            "pagamentos_com_fornecedor": pagamentos_com_fornecedor,
            "pagamentos_com_porte_identificado": fornecedores_com_porte,
            "percentual": (
                fornecedores_com_porte * 100 / pagamentos_com_fornecedor
                if pagamentos_com_fornecedor
                else None
            ),
        },
        "base_analitica": {
            "nome": "tce_pagamentos",
            "base_calculo": "PAGAMENTOS",
            "dimensoes_empenho": ["natureza", "fornecedor"],
        },
        "kpi": "overview_me_mei",
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "escopo": {
            "portes_considerados": list(kpis.PORTES_OVERVIEW),
            "portes_no_denominador_participacao": list(
                kpis.PORTES_COMPRAS_CONSIDERADAS_OVERVIEW
            ),
            "regra_participacao_me": "(ME + MEI) / (ME + MEI + EPP + DEMAIS)",
            "regra_geografica": "ME + MEI",
        },
        "unidade_monetaria": "CENTAVOS",
        **overview,
    }
    if not metadados["data_available"] or base_filtrada.empty:
        pagamento_metadados = tce_observability.tornar_percentuais_indisponiveis(
            pagamento_metadados
        )
    return pagamento_metadados


def consultar_comparacao_tce(
    *,
    codigo_municipio_tce: list[str],
    data_inicial: str,
    data_final: str,
) -> dict[str, Any]:
    """Compara pagamentos de dois a quatro municipios no mesmo periodo."""
    import re

    codigos = [str(codigo).strip() for codigo in codigo_municipio_tce]
    if not 2 <= len(codigos) <= 4:
        raise ComparisonRequestError(
            "INVALID_MUNICIPALITY_COUNT",
            "Informe de dois a quatro municipios para comparar.",
            [{"field": "codigo_municipio_tce", "reason": "invalid_count", "value": len(codigos)}],
            status_code=422,
        )
    invalidos = [codigo for codigo in codigos if not re.fullmatch(r"\d{3}", codigo)]
    if invalidos:
        raise ComparisonRequestError(
            "INVALID_MUNICIPALITY_CODE",
            "Os codigos TCE devem ter exatamente tres digitos.",
            [{"field": "codigo_municipio_tce", "reason": "invalid_value", "value": invalidos}],
        )
    if len(set(codigos)) != len(codigos):
        raise ComparisonRequestError(
            "DUPLICATE_MUNICIPALITY_CODE",
            "Informe cada municipio uma unica vez.",
            [{"field": "codigo_municipio_tce", "reason": "duplicate_value", "value": codigos}],
        )
    inicio, fim = _validar_datas(
        {"start_date": data_inicial, "end_date": data_final},
        lado="periodo",
    )

    municipios = []
    for codigo in codigos:
        municipio = database.localizar_municipio_tce(codigo)
        if municipio is None:
            raise ComparisonRequestError(
                "MUNICIPALITY_NOT_FOUND",
                "Municipio nao encontrado no catalogo local.",
                [{"field": "codigo_municipio_tce", "reason": "not_found", "value": codigo}],
                status_code=404,
            )
        filtros = {
            "uf": municipio["uf"],
            "municipality_ibge_code": municipio["codigo_municipio_ibge"],
            "start_date": inicio.isoformat(),
            "end_date": fim.isoformat(),
            "company_sizes": [],
            "supplier_origins": [],
            "expense_element_codes": [],
        }
        overview = _montar_overview_local(filtros, lado=f"municipios.{codigo}")
        municipios.append({
            "codigo_municipio_tce": codigo,
            "codigo_municipio_ibge": municipio["codigo_municipio_ibge"],
            "nome": municipio["nome"],
            "overview": overview,
        })

    def serie(campo: str, chave: str, valores: tuple[str, ...]) -> list[dict[str, Any]]:
        chaves = list(dict.fromkeys(
            str(registro.get(chave))
            for municipio in municipios
            for registro in municipio["overview"].get(campo, [])
        ))
        resultado = []
        for item_chave in chaves:
            linha: dict[str, Any] = {chave: item_chave, "valores": []}
            for municipio in municipios:
                registro = next((item for item in municipio["overview"].get(campo, [])
                                 if str(item.get(chave)) == item_chave), {})
                if campo == "evolucao_compras_consideradas":
                    valor_map = {
                        "compras_consideradas_centavos": registro.get("compras_consideradas_centavos"),
                        "microempresas_centavos": registro.get("microempresas_centavos"),
                    }
                else:
                    valor_map = {nome: registro.get(nome) for nome in valores}
                linha["valores"].append({
                    "codigo_municipio_tce": municipio["codigo_municipio_tce"],
                    "municipio": municipio["nome"],
                    **valor_map,
                })
            resultado.append(linha)
        return resultado

    indicadores = list(dict.fromkeys(
        indicador
        for municipio in municipios
        for indicador in municipio["overview"].get("kpis", {})
    ))
    kpis_comparados = [
        {
            "indicador": indicador,
            "unidade": "CENTAVOS" if indicador.endswith("_centavos") else "PONTOS_PERCENTUAIS",
            "valores": [
                {
                    "codigo_municipio_tce": municipio["codigo_municipio_tce"],
                    "municipio": municipio["nome"],
                    "valor": municipio["overview"].get("kpis", {}).get(indicador),
                }
                for municipio in municipios
            ],
        }
        for indicador in indicadores
    ]
    portes = serie("participacao_por_porte_empresarial", "porte", ("valor_centavos", "percentual"))
    elementos = serie("elementos_despesa", "codigo", ("valor_final_centavos",))
    destinos = serie("destino_recursos", "destino", ("valor_centavos", "percentual"))
    mensal = serie("evolucao_compras_consideradas", "periodo", ())
    carregamentos = [item["overview"] for item in municipios]
    return {
        "fonte": "TCE-CE/SIM",
        "origem_dados": "BANCO_LOCAL",
        "consultas_externas": False,
        "data_available": all(item.get("data_available", False) for item in carregamentos),
        "is_stale": any(item.get("is_stale", False) for item in carregamentos),
        "stale_reason": list(dict.fromkeys(
            reason for item in carregamentos for reason in item.get("stale_reason", [])
        )),
        "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "parametros": {
            "codigo_municipio_tce": codigos,
            "data_inicial": inicio.isoformat(),
            "data_final": fim.isoformat(),
            "exercicio_orcamentario": inicio.year,
        },
        "municipios": municipios,
        "comparacao": {
            "kpis": kpis_comparados,
            "participacao_por_porte_empresarial": portes,
            "elementos_despesa": elementos,
            "evolucao_compras_consideradas": mensal,
            "destino_recursos": destinos,
        },
        "quality": {
            "municipios": [
                {
                    "codigo_municipio_tce": item["codigo_municipio_tce"],
                    "municipio": item["nome"],
                    "quality": item["overview"].get("quality", {}),
                    "competencias_ausentes": item["overview"].get("competencias_ausentes", []),
                }
                for item in municipios
            ]
        },
    }


__all__ = ["ComparisonRequestError", "consultar_comparacao_tce"]
