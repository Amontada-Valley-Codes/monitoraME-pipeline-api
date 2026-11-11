"""Consultas de snapshots locais usados pelas analises TCE."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core import database
from app.core.config import UF_PADRAO
from app.pipeline import portes_historicos
from app.pipeline.persistence import tce_despesas, tce_pagamentos
from app.utils import normalizar_cnpj


@dataclass(frozen=True)
class SnapshotDespesasTce:
    publicacoes_por_competencia: dict[str, datetime | None]
    falhas: list[dict[str, Any]]
    registros: list[dict[str, Any]]
    fornecedores_cache: dict[str, dict[str, Any]]
    municipio_comprador: str | None
    raizes_com_porte_historico: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotPagamentosTce:
    registros: list[dict[str, Any]]
    competencias_ausentes: list[Any]
    competencias_publicadas: list[str]
    fornecedores_cache: dict[str, dict[str, Any]]
    municipio_comprador: str | None = None


def _cnpjs(registros: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            cnpj
            for registro in registros
            if (cnpj := normalizar_cnpj(registro.get("cnpj_fornecedor")))
        }
    )


def _fornecedores_cache(registros: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cnpjs = _cnpjs(registros)
    return database.listar_fornecedores_cache(cnpjs) if cnpjs else {}


def _raizes_por_competencia(
    registros: list[dict[str, Any]],
) -> dict[str, set[str]]:
    resultado: dict[str, set[str]] = {}
    for registro in registros:
        cnpj = normalizar_cnpj(registro.get("cnpj_fornecedor"))
        competencia = str(registro.get("data_empenho") or "")[:7]
        if cnpj and competencia:
            resultado.setdefault(competencia, set()).add(cnpj[:8])
    return resultado


def carregar_raizes_historicas(
    registros: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Consulta classificacoes historicas para as raizes presentes nos registros."""
    raizes_solicitadas = _raizes_por_competencia(registros)
    if not raizes_solicitadas:
        return {}
    return portes_historicos.raizes_classificadas_por_competencia(raizes_solicitadas)


def carregar_snapshot_despesas(
    *,
    codigo_municipio_tce: str,
    data_inicial: date,
    data_final: date,
    competencias: list[str],
) -> SnapshotDespesasTce:
    """Le somente publicacoes, falhas e dados persistidos do recorte solicitado."""
    publicacoes = tce_despesas.listar_publicacoes_competencias(
        codigo_municipio_tce=codigo_municipio_tce,
        competencias=competencias,
    )
    falhas = tce_despesas.listar_falhas_competencias(
        codigo_municipio_tce=codigo_municipio_tce,
        competencias=competencias,
    )
    competencias_publicadas = set(publicacoes)
    registros = (
        tce_despesas.listar_empenhos_liquidos_publicados(
            codigo_municipio_tce=codigo_municipio_tce,
            data_inicial=data_inicial,
            data_final=data_final,
        )
        if competencias_publicadas
        else []
    )
    registros = [
        registro
        for registro in registros
        if str(registro.get("data_empenho", ""))[:7] in competencias_publicadas
    ]
    municipio = database.localizar_municipio_tce(codigo_municipio_tce)
    return SnapshotDespesasTce(
        publicacoes_por_competencia=publicacoes,
        falhas=falhas,
        registros=registros,
        fornecedores_cache=_fornecedores_cache(registros),
        municipio_comprador=municipio["nome"] if municipio else None,
        raizes_com_porte_historico=carregar_raizes_historicas(registros),
    )


def carregar_snapshot_pagamentos_municipais(
    *,
    codigo_municipio_tce: str,
    data_inicial: date,
    data_final: date,
    exercicio_orcamento: int | None = None,
) -> SnapshotPagamentosTce:
    """Le o snapshot municipal publicado, sem atualizar fontes ou tabelas."""
    competencias_publicadas: list[str] = []
    registros, competencias_ausentes = (
        tce_pagamentos.listar_pagamentos_municipais_para_analise(
            codigo_municipio_tce=codigo_municipio_tce,
            data_inicial=data_inicial,
            data_final=data_final,
            exercicio_orcamento=exercicio_orcamento,
            competencias_publicadas=competencias_publicadas,
        )
    )
    municipio = database.localizar_municipio_tce(codigo_municipio_tce)
    return SnapshotPagamentosTce(
        registros=registros,
        competencias_ausentes=competencias_ausentes,
        competencias_publicadas=sorted(set(competencias_publicadas)),
        fornecedores_cache=_fornecedores_cache(registros),
        municipio_comprador=municipio["nome"] if municipio else None,
    )


def carregar_snapshot_pagamentos_estaduais(
    *,
    data_inicial: date,
    data_final: date,
    exercicio_orcamento: int | None = None,
) -> SnapshotPagamentosTce:
    """Le o snapshot estadual publicado, sem atualizar fontes ou tabelas."""
    competencias_publicadas: list[str] = []
    registros, competencias_ausentes = (
        tce_pagamentos.listar_pagamentos_estaduais_para_analise(
            data_inicial=data_inicial,
            data_final=data_final,
            uf=UF_PADRAO,
            exercicio_orcamento=exercicio_orcamento,
            competencias_publicadas=competencias_publicadas,
        )
    )
    return SnapshotPagamentosTce(
        registros=registros,
        competencias_ausentes=competencias_ausentes,
        competencias_publicadas=sorted(set(competencias_publicadas)),
        fornecedores_cache=_fornecedores_cache(registros),
    )


def contar_municipios_catalogados(uf: str) -> int:
    """Conta municipios no catalogo local; nao consulta o IBGE."""
    return len(database.listar_municipios_ibge(uf))


__all__ = [
    "SnapshotDespesasTce",
    "SnapshotPagamentosTce",
    "carregar_snapshot_despesas",
    "carregar_raizes_historicas",
    "carregar_snapshot_pagamentos_estaduais",
    "carregar_snapshot_pagamentos_municipais",
    "contar_municipios_catalogados",
]
