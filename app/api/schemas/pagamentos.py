from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import (
    CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE,
    CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS,
    VERSAO_METODOLOGICA_PAGAMENTOS_TCE,
)


class QualidadePublicacaoStatus(StrEnum):
    APROVADA = "APROVADA"
    BLOQUEADA = "BLOQUEADA"
    PARCIAL = "PARCIAL"


class CategoriaReconciliacaoPagamento(StrEnum):
    ME = "ME"
    EPP = "EPP"
    DEMAIS = "DEMAIS"
    NAO_IDENTIFICADO = "NAO_IDENTIFICADO"
    CPF = "CPF"
    DOCUMENTO_INVALIDO = "DOCUMENTO_INVALIDO"


class _SchemaPagamentos(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EscopoPagamentosTce(_SchemaPagamentos):
    codigo_municipio_tce: str = Field(pattern=r"^\d{3}$")
    codigo_municipio_ibge: str = Field(pattern=r"^\d{7}$")
    municipio: str = Field(min_length=1)
    exercicio_orcamento: int = Field(ge=100000, le=999900, multiple_of=100)
    competencia_corte: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    medida_financeira: Literal["valor_nota_pagamento"]
    porte_publico: Literal["ME"]


class TotaisPagamentosTce(_SchemaPagamentos):
    total_financeiro_centavos: int = Field(ge=0)
    total_me_centavos: int = Field(ge=0)
    participacao_me_percentual: float | None = Field(default=None, ge=0, le=100)


class ElementoPagamentosTce(_SchemaPagamentos):
    codigo: str
    nome: str = Field(min_length=1)
    total_financeiro_centavos: int = Field(ge=0)
    total_me_centavos: int = Field(ge=0)
    participacao_me_percentual: float | None = Field(default=None, ge=0, le=100)

    @field_validator("codigo")
    @classmethod
    def validar_codigo(cls, valor: str) -> str:
        if valor not in CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS:
            raise ValueError("codigo deve ser um dos sete elementos monitorados.")
        return valor


class ReconciliacaoPagamentosTce(_SchemaPagamentos):
    categoria: CategoriaReconciliacaoPagamento
    valor_centavos: int = Field(ge=0)


class QualidadePagamentosTce(_SchemaPagamentos):
    status: QualidadePublicacaoStatus
    pagamentos_processados: int = Field(ge=0)
    pagamentos_orfaos: int = Field(ge=0)
    duplicidades_conflitantes: int = Field(ge=0)
    classificacoes_pendentes: int = Field(ge=0)
    financeiro_reconciliado: bool


class ReferenciaCadastralPagamentosTce(_SchemaPagamentos):
    fonte: str = Field(min_length=1)
    referencia: str = Field(min_length=1)
    consultado_em: datetime
    procedencia_temporal: str = Field(min_length=1)


class TcePagamentosResponse(_SchemaPagamentos):
    escopo: EscopoPagamentosTce
    totais: TotaisPagamentosTce
    elementos: list[ElementoPagamentosTce] = Field(min_length=7, max_length=7)
    reconciliacao: list[ReconciliacaoPagamentosTce] = Field(min_length=6, max_length=6)
    qualidade: QualidadePagamentosTce
    referencia_cadastral: ReferenciaCadastralPagamentosTce
    versao_metodologica: Literal[VERSAO_METODOLOGICA_PAGAMENTOS_TCE]
    atualizado_em: datetime

    @field_validator("elementos")
    @classmethod
    def validar_elementos(
        cls,
        elementos: list[ElementoPagamentosTce],
    ) -> list[ElementoPagamentosTce]:
        codigos = [item.codigo for item in elementos]
        if len(set(codigos)) != len(codigos):
            raise ValueError("elementos nao podem conter codigos duplicados.")
        if set(codigos) != set(CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS):
            raise ValueError("elementos devem conter os sete codigos monitorados.")
        return elementos

    @field_validator("reconciliacao")
    @classmethod
    def validar_reconciliacao(
        cls,
        reconciliacao: list[ReconciliacaoPagamentosTce],
    ) -> list[ReconciliacaoPagamentosTce]:
        categorias = [item.categoria.value for item in reconciliacao]
        if len(set(categorias)) != len(categorias):
            raise ValueError("reconciliacao nao pode conter categorias duplicadas.")
        if set(categorias) != set(CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE):
            raise ValueError("reconciliacao deve conter todas as categorias previstas.")
        return reconciliacao


__all__ = [
    "CategoriaReconciliacaoPagamento",
    "ElementoPagamentosTce",
    "EscopoPagamentosTce",
    "QualidadePagamentosTce",
    "QualidadePublicacaoStatus",
    "ReconciliacaoPagamentosTce",
    "ReferenciaCadastralPagamentosTce",
    "TcePagamentosResponse",
    "TotaisPagamentosTce",
]
