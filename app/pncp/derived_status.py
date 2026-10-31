"""Regra deterministica de situacao operacional PNCP, independente da coleta."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import PncpEvento, PncpItem, PncpOportunidade
from app.core import orm

logger = logging.getLogger(__name__)
RULE_VERSION = "pncp-status-v1"
ENCERRANDO_EM_HORAS = 48


def _texto(valor: Any) -> str:
    if isinstance(valor, dict):
        valor = valor.get("nome") or valor.get("descricao") or valor.get("valor") or valor
    return str(valor or "").strip().upper()


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, dict):
        valor = valor.get("valor")
    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, str):
        try:
            dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # PNCP timestamps sem offset nao carregam fuso suficiente para decisao.
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def inferir_status_resultado_itens(estados: list[str]) -> str | None:
    """Nao confunde resultado coletado parcialmente com homologacao parcial."""
    if not estados:
        return None
    homologados = sum(estado == "FINAL" for estado in estados)
    if homologados == len(estados):
        return "HOMOLOGADA_TOTAL"
    if homologados:
        return "HOMOLOGADA_PARCIAL"
    if any(estado == "PARCIAL" for estado in estados):
        return "RESULTADO_PUBLICADO"
    return None


def avaliar_status(*, official_status: Any, inicio: Any, encerramento: Any,
                   modalidade: Any = None, forma_participacao: Any = None,
                   resultado_estado: str | None = None,
                   agora: datetime) -> dict[str, Any]:
    """Tabela v1: impeditivo > resultado/homologacao > janela temporal.

    Ambiguidade ou datas invalidas produzem INDETERMINADO, sem inferencia.
    """
    if agora.tzinfo is None:
        raise ValueError("agora deve incluir timezone")
    agora = agora.astimezone(timezone.utc)
    official = _texto(official_status)
    start, end = _instante(inicio), _instante(encerramento)
    reasons: list[str] = []
    impeditivos = (("ANUL", "ANULADA"), ("REVOG", "REVOGADA"), ("SUSPEN", "SUSPENSA"))
    for token, status in impeditivos:
        if token in official:
            return {"official_status": official_status, "derived_status": status,
                    "reason_codes": [f"STATUS_OFICIAL_{status}"], "data_confidence": "ALTA",
                    "evaluated_at": agora, "rule_version": RULE_VERSION}
    if resultado_estado in {"RESULTADO_PUBLICADO", "HOMOLOGADA_PARCIAL", "HOMOLOGADA_TOTAL"} or any(t in official for t in ("HOMOLOG", "ADJUDIC", "RESULTADO")):
        parcial = resultado_estado == "HOMOLOGADA_PARCIAL" or any(t in official for t in ("PARCIAL", "PARCIALMENTE"))
        total = resultado_estado == "HOMOLOGADA_TOTAL" or "HOMOLOG" in official and not parcial
        status = "HOMOLOGADA_PARCIAL" if parcial else "HOMOLOGADA_TOTAL" if total else "RESULTADO_PUBLICADO"
        return {"official_status": official_status, "derived_status": status,
                "reason_codes": ["RESULTADO_PARCIAL" if parcial else "RESULTADO_OFICIAL"],
                "data_confidence": "ALTA", "evaluated_at": agora, "rule_version": RULE_VERSION}
    if any(t in official for t in ("ENCERR", "FECHAD")):
        return {"official_status": official_status, "derived_status": "ENCERRADA",
                "reason_codes": ["STATUS_OFICIAL_ENCERRADO"], "data_confidence": "ALTA",
                "evaluated_at": agora, "rule_version": RULE_VERSION}
    if not start or not end:
        reasons.append("DATA_AUSENTE_OU_SEM_FUSO")
        status, confidence = "INDETERMINADO", "BAIXA"
    elif start and start > end:
        reasons.append("JANELA_INVERTIDA")
        status, confidence = "INDETERMINADO", "BAIXA"
    elif "SUSPEN" in official or "ANUL" in official or "REVOG" in official:
        status, confidence = "INDETERMINADO", "BAIXA"
        reasons.append("SITUACAO_OFICIAL_CONTRADITORIA")
    elif start is not None and agora < start:
        status, confidence = "ABERTURA_FUTURA", "ALTA"
        reasons.append("ANTES_DO_INICIO_EFETIVO")
    elif agora < end:
        status, confidence = ("ENCERRANDO_EM_BREVE" if (end - agora).total_seconds() <= ENCERRANDO_EM_HORAS * 3600 else "ABERTA"), "ALTA"
        reasons.append("DENTRO_DA_JANELA_EFETIVA")
    else:
        status, confidence = "ENCERRADA", "ALTA"
        reasons.append("PRAZO_EFETIVO_EXPIRADO")
    # Divulgacao e forma/modalidade so qualificam, nunca abrem a janela.
    if official and not any(t in official for t in ("ABERT", "RECEB")):
        reasons.append("STATUS_OFICIAL_NAO_CONFIRMA_ABERTURA")
    if modalidade or forma_participacao:
        reasons.append("MODALIDADE_FORMA_PRESERVADAS_PARA_AUDITORIA")
    return {"official_status": official_status, "derived_status": status,
            "reason_codes": sorted(set(reasons)), "data_confidence": confidence,
            "evaluated_at": agora, "rule_version": RULE_VERSION}


def aplicar_status_session(session: Session, oportunidade: PncpOportunidade, *, agora: datetime,
                           official_status: Any = None, inicio: Any = None,
                           encerramento: Any = None, modalidade: Any = None,
                           forma_participacao: Any = None, resultado_estado: str | None = None) -> bool:
    antes = {"official_status": oportunidade.official_status,
             "derived_status": oportunidade.derived_status,
             "reason_codes": oportunidade.reason_codes or [],
             "data_confidence": oportunidade.data_confidence}
    oficial = oportunidade.situacao if official_status is None else official_status
    prazo = encerramento if encerramento is not None else oportunidade.prazo_proposta_final
    resultado = avaliar_status(official_status=oficial, inicio=inicio, encerramento=prazo,
        modalidade=modalidade if modalidade is not None else oportunidade.modalidade,
        forma_participacao=forma_participacao, resultado_estado=resultado_estado, agora=agora)
    oportunidade.official_status = str(oficial) if oficial is not None else None
    oportunidade.derived_status = resultado["derived_status"]
    oportunidade.reason_codes = resultado["reason_codes"]
    oportunidade.data_confidence = resultado["data_confidence"]
    oportunidade.evaluated_at = resultado["evaluated_at"]
    oportunidade.status_rule_version = resultado["rule_version"]
    depois = {k: resultado[k] for k in ("official_status", "derived_status", "reason_codes", "data_confidence")}
    if antes == depois:
        return False
    if all(antes.get(campo) == depois.get(campo)
           for campo in ("derived_status", "reason_codes", "data_confidence")):
        # Mudanca textual oficial sem efeito operacional nao gera alerta.
        return True
    canonical = json.dumps([oportunidade.source, oportunidade.external_id, oportunidade.payload_hash,
        resultado["rule_version"], depois], sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    key = hashlib.sha256(canonical.encode()).hexdigest()
    session.execute(insert(PncpEvento).values(event_key=key, event_type="STATUS_OPERACIONAL_ALTERADO",
        source=oportunidade.source, external_id=oportunidade.external_id, payload_hash=oportunidade.payload_hash,
        details={"event_id": key, "entity_id": oportunidade.external_id, "version": oportunidade.payload_hash,
          "correlation_id": oportunidade.payload_hash, "type": "STATUS_OPERACIONAL_ALTERADO",
          "before": antes, "after": depois, "observed_at": resultado["evaluated_at"].isoformat(),
          "source": "PNCP", "idempotency_key": key, "rule_version": RULE_VERSION})
        .on_conflict_do_nothing(constraint="uq_pncp_eventos_event_key"))
    return True


def reavaliar_status_persistidos(*, agora: datetime | None = None) -> int:
    """Relogio operacional: reavalia a visao local sem chamadas ao PNCP."""
    agora = agora or datetime.now(timezone.utc)
    alterados = 0
    with orm.main_session() as session:
        estados_itens: dict[tuple[str, str], list[str]] = {}
        for source, external_id, estado in session.execute(select(
                PncpItem.source, PncpItem.external_id, PncpItem.resultado_status)):
            estados_itens.setdefault((source, external_id), []).append(estado)
        for oportunidade in session.scalars(select(PncpOportunidade)).yield_per(250):
            semanticos = oportunidade.dados_semanticos or {}
            estados = estados_itens.get((oportunidade.source, oportunidade.external_id), [])
            resultado_geral = inferir_status_resultado_itens(estados)
            if aplicar_status_session(session, oportunidade, agora=agora,
                    inicio=semanticos.get("data_abertura_proposta"),
                    encerramento=semanticos.get("prazo_proposta_final_origem"),
                    forma_participacao=semanticos.get("forma_participacao_origem"),
                    resultado_estado=resultado_geral):
                alterados += 1
    return alterados
