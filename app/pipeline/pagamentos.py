"""Conciliação e agregação determinística dos pagamentos do TCE-CE."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterable

from app.api.schemas.pagamentos import TcePagamentosResponse
from app.core.config import (
    CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE,
    CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS,
    ESTADO_NAO_ESTORNADO_PAGAMENTOS_TCE,
    MEDIDA_FINANCEIRA_PAGAMENTOS_TCE,
    NATUREZAS_DESPESA_MONITORADAS,
    VERSAO_METODOLOGICA_PAGAMENTOS_TCE,
)


ClassificadorCadastral = Callable[[str, str], dict[str, Any] | None]


@dataclass(frozen=True)
class ResultadoPagamentos:
    resposta: TcePagamentosResponse
    pagamentos_conciliados: list[dict[str, Any]]
    problemas: list[dict[str, Any]]

    @property
    def publicavel(self) -> bool:
        return not self.problemas and self.resposta.qualidade.status != "BLOQUEADA"


def _assinatura(registro: dict[str, Any]) -> str:
    return json.dumps(registro, sort_keys=True, ensure_ascii=False, default=str)


def _deduplicar(
    registros: Iterable[dict[str, Any]], campo: str
) -> tuple[list[dict[str, Any]], list[str]]:
    unicos: dict[str, dict[str, Any]] = {}
    conflitos: list[str] = []
    for registro in registros:
        chave = str(registro.get(campo) or "")
        anterior = unicos.get(chave)
        if anterior is None:
            unicos[chave] = registro
        elif _assinatura(anterior) != _assinatura(registro):
            conflitos.append(chave)
    return list(unicos.values()), sorted(set(conflitos))


def _categoria_porte(classificacao: dict[str, Any] | None) -> str:
    if not classificacao:
        return "NAO_IDENTIFICADO"
    codigo = classificacao.get("porte_codigo")
    porte = str(classificacao.get("porte_padronizado") or "").upper()
    if codigo == "01" or porte in {"ME", "MEI"}:
        return "ME"
    if codigo == "03" or porte == "EPP":
        return "EPP"
    if codigo == "05" or porte == "DEMAIS":
        return "DEMAIS"
    return "NAO_IDENTIFICADO"


def _percentual(parte: int, total: int) -> float | None:
    return round(parte * 100 / total, 4) if total else None


def processar_pagamentos(
    *,
    codigo_municipio_tce: str,
    codigo_municipio_ibge: str,
    municipio: str,
    exercicio_orcamento: int,
    competencia_corte: str,
    orgaos: Iterable[dict[str, Any]],
    pagamentos: Iterable[dict[str, Any]],
    empenhos: Iterable[dict[str, Any]],
    classificar_cnpj: ClassificadorCadastral | None = None,
    referencia_cadastral: dict[str, Any] | None = None,
    atualizado_em: datetime | None = None,
) -> ResultadoPagamentos:
    """Produz uma publicação; inconsistências financeiras tornam-na bloqueada."""
    instante = atualizado_em or datetime.now(timezone.utc)
    referencia = {
        "fonte": "NAO_DISPONIVEL",
        "referencia": competencia_corte,
        "consultado_em": instante,
        "procedencia_temporal": "NAO_COMPROVADA",
        **(referencia_cadastral or {}),
    }
    problemas: list[dict[str, Any]] = []

    orgaos_por_codigo = {str(item["codigo_orgao"]): item for item in orgaos}
    pagamentos_unicos, conflitos = _deduplicar(pagamentos, "chave_pagamento")
    for chave in conflitos:
        problemas.append({"codigo": "DUPLICIDADE_CONFLITANTE", "chave": chave})

    empenhos_por_chave: dict[str, list[dict[str, Any]]] = defaultdict(list)
    assinaturas_empenho: dict[str, set[str]] = defaultdict(set)
    for empenho in empenhos:
        chave = str(empenho.get("chave_empenho") or "")
        assinatura = _assinatura(empenho)
        if assinatura not in assinaturas_empenho[chave]:
            empenhos_por_chave[chave].append(empenho)
            assinaturas_empenho[chave].add(assinatura)

    conciliados: list[dict[str, Any]] = []
    pagamentos_orfaos = 0
    classificacoes_pendentes = 0
    temporal_nao_comprovada = False
    totais_categoria = {categoria: 0 for categoria in CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE}
    totais_elemento = {
        codigo: {"total": 0, "me": 0} for codigo in CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS
    }

    for pagamento in pagamentos_unicos:
        if int(pagamento["estado_de_estornado"]) != ESTADO_NAO_ESTORNADO_PAGAMENTOS_TCE:
            continue
        if str(pagamento["competencia_documento"]) > competencia_corte:
            continue
        orgao = orgaos_por_codigo.get(str(pagamento["codigo_orgao"]))
        if orgao is None:
            problemas.append({
                "codigo": "ORGAO_NAO_CADASTRADO",
                "codigo_orgao": pagamento["codigo_orgao"],
            })
            continue
        if orgao["administracao"] == "CAMARA":
            continue

        candidatos = empenhos_por_chave.get(str(pagamento["chave_empenho"]), [])
        if not candidatos:
            pagamentos_orfaos += 1
            problemas.append({
                "codigo": "PAGAMENTO_ORFAO",
                "chave_pagamento": pagamento["chave_pagamento"],
            })
            continue
        if len(candidatos) != 1:
            problemas.append({
                "codigo": "EMPENHO_AMBIGUO",
                "chave_pagamento": pagamento["chave_pagamento"],
                "quantidade": len(candidatos),
            })
            continue
        empenho = candidatos[0]
        elemento = str(empenho["codigo_elemento_despesa"])
        if elemento not in totais_elemento:
            continue
        valor = int(pagamento["valor_pagamento_centavos"])
        if valor < 0:
            problemas.append({
                "codigo": "VALOR_PAGAMENTO_NEGATIVO",
                "chave_pagamento": pagamento["chave_pagamento"],
            })
            continue

        cnpj = empenho.get("cnpj_fornecedor")
        cpf = empenho.get("cpf_fornecedor")
        documento = empenho.get("documento_fornecedor")
        classificacao: dict[str, Any] | None = None
        if cpf:
            categoria = "CPF"
        elif not cnpj:
            categoria = "DOCUMENTO_INVALIDO"
            classificacoes_pendentes += 1
        else:
            classificacao = (
                classificar_cnpj(str(cnpj), str(pagamento["competencia_pagamento"]))
                if classificar_cnpj
                else None
            )
            categoria = _categoria_porte(classificacao)
            if categoria == "NAO_IDENTIFICADO":
                classificacoes_pendentes += 1
            if not classificacao or not classificacao.get("temporal_comprovada", False):
                temporal_nao_comprovada = True

        totais_categoria[categoria] += valor
        totais_elemento[elemento]["total"] += valor
        if categoria == "ME":
            totais_elemento[elemento]["me"] += valor
        conciliados.append({
            **pagamento,
            "codigo_elemento_despesa": elemento,
            "documento_fornecedor": documento,
            "cnpj_fornecedor": cnpj,
            "raiz_cnpj_fornecedor": str(cnpj)[:8] if cnpj else None,
            "cpf_fornecedor": cpf,
            "categoria_fornecedor": categoria,
            "classificacao_cadastral": classificacao,
        })

    total = sum(totais_categoria.values())
    total_elementos = sum(item["total"] for item in totais_elemento.values())
    financeiro_reconciliado = total == total_elementos
    if not financeiro_reconciliado:
        problemas.append({"codigo": "FINANCEIRO_NAO_RECONCILIADO"})
    total_me = totais_categoria["ME"]
    status = "BLOQUEADA" if problemas else (
        "PARCIAL" if classificacoes_pendentes or temporal_nao_comprovada else "APROVADA"
    )

    resposta = TcePagamentosResponse.model_validate({
        "escopo": {
            "codigo_municipio_tce": codigo_municipio_tce,
            "codigo_municipio_ibge": codigo_municipio_ibge,
            "municipio": municipio,
            "exercicio_orcamento": exercicio_orcamento,
            "competencia_corte": competencia_corte,
            "medida_financeira": MEDIDA_FINANCEIRA_PAGAMENTOS_TCE,
            "porte_publico": "ME",
        },
        "totais": {
            "total_financeiro_centavos": total,
            "total_me_centavos": total_me,
            "participacao_me_percentual": _percentual(total_me, total),
        },
        "elementos": [
            {
                "codigo": codigo,
                "nome": nome,
                "total_financeiro_centavos": totais_elemento[codigo]["total"],
                "total_me_centavos": totais_elemento[codigo]["me"],
                "participacao_me_percentual": _percentual(
                    totais_elemento[codigo]["me"], totais_elemento[codigo]["total"]
                ),
            }
            for codigo, nome in NATUREZAS_DESPESA_MONITORADAS.items()
        ],
        "reconciliacao": [
            {"categoria": categoria, "valor_centavos": totais_categoria[categoria]}
            for categoria in CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE
        ],
        "qualidade": {
            "status": status,
            "pagamentos_processados": len(conciliados),
            "pagamentos_orfaos": pagamentos_orfaos,
            "duplicidades_conflitantes": len(conflitos),
            "classificacoes_pendentes": classificacoes_pendentes,
            "financeiro_reconciliado": financeiro_reconciliado,
        },
        "referencia_cadastral": referencia,
        "versao_metodologica": VERSAO_METODOLOGICA_PAGAMENTOS_TCE,
        "atualizado_em": instante,
    })
    return ResultadoPagamentos(resposta, conciliados, problemas)


__all__ = ["ResultadoPagamentos", "processar_pagamentos"]
