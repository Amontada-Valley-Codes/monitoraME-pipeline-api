from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.pipeline import portes_historicos


ORIGENS_CONHECIDAS = {
    "NO_MUNICIPIO_COMPRADOR",
    "EM_OUTRO_MUNICIPIO",
    "FORA_DO_CEARA",
    "Sediado no município comprador",
    "Outro município do Ceará",
    "Fora do estado",
}

MENSAGENS_QUALIDADE = {
    "DATA_UNAVAILABLE": "Não há competência publicada para o recorte solicitado.",
    "PARTIAL_PERIOD": (
        "O recorte contém competências ausentes, cobertura municipal incompleta "
        "ou datas fora de meses completos."
    ),
    "LATEST_LOAD_FAILED": (
        "A carga mais recente falhou; os valores preservam o último snapshot publicado."
    ),
    "STALE_SNAPSHOT": "A publicação ultrapassou a janela de atualização esperada.",
    "PUBLICATION_TIMESTAMP_MISSING": (
        "A publicação não possui instante de sucesso; não é possível confirmar sua defasagem."
    ),
    "UNKNOWN_ORIGIN": "Há despesas sem origem geográfica identificada.",
    "HISTORICAL_SIZE_COVERAGE_INCOMPLETE": (
        "A classificação histórica de porte não cobre todas as despesas do período."
    ),
}


def _data_hora(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        resultado = valor
    elif isinstance(valor, str) and valor:
        try:
            resultado = datetime.fromisoformat(valor)
        except ValueError:
            return None
    else:
        return None
    if resultado.tzinfo is None:
        return resultado.replace(tzinfo=timezone.utc)
    return resultado.astimezone(timezone.utc)


def _fim_competencia(valor: str) -> date:
    ano, mes = (int(parte) for parte in valor.split("-", maxsplit=1))
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def _ttl_competencia(competencia: str, agora: datetime) -> timedelta:
    ano, mes = (int(parte) for parte in competencia.split("-", maxsplit=1))
    distancia_meses = (agora.year - ano) * 12 + agora.month - mes
    if distancia_meses == 0:
        return timedelta(hours=1)
    if 1 <= distancia_meses <= 3:
        return timedelta(hours=24)
    return timedelta(days=7)


def _valor_centavos(registro: dict[str, Any]) -> int:
    try:
        return int(registro.get("valor_liquido_centavos") or 0)
    except (TypeError, ValueError):
        return 0


def _origem_desconhecida(registro: dict[str, Any]) -> bool:
    origem = registro.get("origem_fornecedor") or registro.get("origem_geografica")
    if origem is None:
        return True
    return str(origem).strip() not in ORIGENS_CONHECIDAS


def construir_metadados_carga(
    *,
    data_inicial: date,
    data_final: date,
    competencias_solicitadas: list[str],
    publicacoes: list[dict[str, Any]],
    falhas: list[dict[str, Any]],
    registros: list[dict[str, Any]],
    municipios_no_escopo: int,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Cria o contrato comum de disponibilidade, defasagem e qualidade do TCE."""
    instante = agora or datetime.now(timezone.utc)
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    else:
        instante = instante.astimezone(timezone.utc)

    publicados_por_particao = {
        (
            str(item.get("codigo_municipio_tce") or ""),
            str(item["competencia"]),
        ): _data_hora(item.get("publicado_em"))
        for item in publicacoes
    }
    competencias_publicadas = {
        competencia for _, competencia in publicados_por_particao
    }
    competencias_ausentes = [
        competencia
        for competencia in competencias_solicitadas
        if competencia not in competencias_publicadas
    ]
    particoes_por_competencia: dict[str, set[str]] = {}
    for codigo, competencia in publicados_por_particao:
        particoes_por_competencia.setdefault(competencia, set()).add(codigo)
    competencias_com_cobertura_municipal_incompleta = [
        competencia
        for competencia, municipios in particoes_por_competencia.items()
        if municipios_no_escopo > len(municipios)
    ]
    instante_publicacao = max(
        (valor for valor in publicados_por_particao.values() if valor is not None),
        default=None,
    )
    datas_publicadas = sorted(competencias_publicadas)
    data_as_of = (
        min(data_final, _fim_competencia(datas_publicadas[-1])).isoformat()
        if datas_publicadas
        else None
    )

    falha_mais_recente = False
    falhas_por_particao: dict[tuple[str, str], datetime] = {}
    for item in falhas:
        chave = (
            str(item.get("codigo_municipio_tce") or ""),
            str(item.get("competencia") or ""),
        )
        finalizado = _data_hora(item.get("finalizado_em"))
        if finalizado is not None:
            anterior = falhas_por_particao.get(chave)
            if anterior is None or finalizado > anterior:
                falhas_por_particao[chave] = finalizado
    for chave, falhou_em in falhas_por_particao.items():
        publicado_em = publicados_por_particao.get(chave)
        if publicado_em is None or falhou_em >= publicado_em:
            falha_mais_recente = True
            break

    snapshot_vencido = any(
        publicado_em is not None
        and instante - publicado_em
        > _ttl_competencia(competencia, instante)
        for (_, competencia), publicado_em in publicados_por_particao.items()
    )
    publicacao_sem_instante = any(
        publicado_em is None for publicado_em in publicados_por_particao.values()
    )
    periodo_parcial = bool(
        competencias_ausentes
        or competencias_com_cobertura_municipal_incompleta
        or data_inicial.day != 1
        or data_final.day != calendar.monthrange(data_final.year, data_final.month)[1]
    )

    despesas_origem_desconhecida = [
        registro for registro in registros if _origem_desconhecida(registro)
    ]
    total_despesas = len(registros)
    total_origem_desconhecida = len(despesas_origem_desconhecida)
    despesas_com_cnpj_por_competencia: dict[str, set[str]] = {}
    for registro in registros:
        cnpj = str(registro.get("cnpj_fornecedor") or "").strip()
        competencia = str(registro.get("data_empenho") or "")[:7]
        if len(cnpj) >= 8 and competencia:
            despesas_com_cnpj_por_competencia.setdefault(competencia, set()).add(cnpj[:8])

    raizes_com_porte_historico = (
        portes_historicos.raizes_classificadas_por_competencia(
            despesas_com_cnpj_por_competencia
        )
        if despesas_com_cnpj_por_competencia
        else {}
    )
    despesas_classificadas_historicamente = sum(
        1
        for registro in registros
        if len(str(registro.get("cnpj_fornecedor") or "").strip()) >= 8
        and str(registro.get("cnpj_fornecedor"))[:8]
        in raizes_com_porte_historico.get(
            str(registro.get("data_empenho") or "")[:7], set()
        )
    )

    motivos: list[str] = []
    if falha_mais_recente:
        motivos.append("LATEST_LOAD_FAILED")
    if snapshot_vencido:
        motivos.append("SNAPSHOT_EXPIRED")
    if publicacao_sem_instante:
        motivos.append("PUBLICATION_TIMESTAMP_MISSING")

    alertas: list[dict[str, str]] = []
    if not publicados_por_particao:
        alertas.append({
            "code": "DATA_UNAVAILABLE",
            "message": MENSAGENS_QUALIDADE["DATA_UNAVAILABLE"],
        })
    if periodo_parcial:
        alertas.append({
            "code": "PARTIAL_PERIOD",
            "message": MENSAGENS_QUALIDADE["PARTIAL_PERIOD"],
        })
    if falha_mais_recente:
        alertas.append({
            "code": "LATEST_LOAD_FAILED",
            "message": MENSAGENS_QUALIDADE["LATEST_LOAD_FAILED"],
        })
    if snapshot_vencido:
        alertas.append({
            "code": "STALE_SNAPSHOT",
            "message": MENSAGENS_QUALIDADE["STALE_SNAPSHOT"],
        })
    if publicacao_sem_instante:
        alertas.append({
            "code": "PUBLICATION_TIMESTAMP_MISSING",
            "message": MENSAGENS_QUALIDADE["PUBLICATION_TIMESTAMP_MISSING"],
        })
    if total_origem_desconhecida:
        alertas.append({
            "code": "UNKNOWN_ORIGIN",
            "message": MENSAGENS_QUALIDADE["UNKNOWN_ORIGIN"],
        })
    if (
        total_despesas
        and despesas_classificadas_historicamente < total_despesas
    ):
        alertas.append({
            "code": "HISTORICAL_SIZE_COVERAGE_INCOMPLETE",
            "message": MENSAGENS_QUALIDADE[
                "HISTORICAL_SIZE_COVERAGE_INCOMPLETE"
            ],
        })

    return {
        "data_available": bool(publicados_por_particao),
        "data_as_of": data_as_of,
        "last_successful_load_at": (
            instante_publicacao.isoformat() if instante_publicacao else None
        ),
        "is_stale": bool(
            falha_mais_recente or snapshot_vencido or publicacao_sem_instante
        ),
        "stale_reason": motivos,
        "quality": {
            "municipalities_with_data": len({
                codigo for codigo, _ in publicados_por_particao if codigo
            }),
            "municipalities_in_scope": municipios_no_escopo,
            "unknown_origin": {
                "expenses": total_origem_desconhecida,
                "value_centavos": sum(
                    _valor_centavos(registro)
                    for registro in despesas_origem_desconhecida
                ),
                "percent": (
                    round(total_origem_desconhecida * 100 / total_despesas, 6)
                    if total_despesas
                    else None
                ),
            },
            "historical_size_classification_coverage": {
                "classified_expenses": despesas_classificadas_historicamente,
                "expenses_in_scope": total_despesas,
                "classification_unit": "CNPJ_BASICO",
                "reference": "EXACT_MONTHLY_COMPETENCE",
                "percent": (
                    round(despesas_classificadas_historicamente * 100 / total_despesas, 6)
                    if total_despesas
                    else None
                ),
            },
            "partial_period": periodo_parcial,
            "missing_competencies": competencias_ausentes,
            "competencies_with_incomplete_municipal_coverage": (
                competencias_com_cobertura_municipal_incompleta
            ),
            "alerts": alertas,
        },
    }


def tornar_percentuais_indisponiveis(valor: Any) -> Any:
    """Evita apresentar taxas como zero quando não há dados para calculá-las."""
    if isinstance(valor, dict):
        resultado: dict[str, Any] = {}
        for chave, item in valor.items():
            chave_normalizada = str(chave).lower()
            if any(
                termo in chave_normalizada
                for termo in ("percent", "taxa", "retencao", "participacao", "evasao")
            ) and not isinstance(item, (dict, list)):
                resultado[chave] = None
            else:
                resultado[chave] = tornar_percentuais_indisponiveis(item)
        return resultado
    if isinstance(valor, list):
        return [tornar_percentuais_indisponiveis(item) for item in valor]
    return valor


__all__ = [
    "construir_metadados_carga",
    "tornar_percentuais_indisponiveis",
]
