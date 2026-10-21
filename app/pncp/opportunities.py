"""Contrato local de oportunidades, matching e eventos PNCP para consumidores."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import PncpEnriquecimento, PncpEvento, PncpMatching, PncpOportunidade


def contrato_consumo(oportunidade: PncpOportunidade, enriquecimento: PncpEnriquecimento | None = None) -> dict[str, Any]:
    """DTO persistido para NestJS/frontend; nunca chama o PNCP."""
    return {
        "id": oportunidade.external_id, "source": oportunidade.source,
        "url_oficial": oportunidade.url_oficial, "orgao": oportunidade.orgao,
        "unidade": oportunidade.unidade, "objeto": oportunidade.objeto,
        "modalidade": oportunidade.modalidade, "situacao": oportunidade.situacao,
        "prazo_proposta_final": oportunidade.prazo_proposta_final,
        "atualizado_em": oportunidade.source_updated_at or oportunidade.normalized_at,
        "itens_disponiveis": bool(enriquecimento and enriquecimento.items_payload),
        "anexos_disponiveis": bool(enriquecimento and enriquecimento.documents_payload),
    }


def registrar_evento_session(session: Session, *, event_type: str, oportunidade: PncpOportunidade, details: dict[str, Any]) -> bool:
    chave = hashlib.sha256(f"{event_type}:{oportunidade.source}:{oportunidade.external_id}:{oportunidade.payload_hash}".encode()).hexdigest()
    evento_id = session.scalar(insert(PncpEvento).values(
        event_key=chave, event_type=event_type, source=oportunidade.source,
        external_id=oportunidade.external_id, payload_hash=oportunidade.payload_hash, details=details,
    ).on_conflict_do_nothing(constraint="uq_pncp_eventos_event_key").returning(PncpEvento.id))
    return evento_id is not None


def registrar_eventos_alteracao_session(session: Session, *, anterior: PncpOportunidade | None, atual: PncpOportunidade) -> None:
    if anterior is None:
        registrar_evento_session(session, event_type="CONTRATACAO_NOVA", oportunidade=atual, details={})
        return
    if anterior.prazo_proposta_final != atual.prazo_proposta_final:
        registrar_evento_session(session, event_type="PRAZO_ALTERADO", oportunidade=atual, details={"anterior": str(anterior.prazo_proposta_final), "atual": str(atual.prazo_proposta_final)})
    texto = json.dumps(atual.situacao, ensure_ascii=False).upper()
    if any(valor in texto for valor in ("SUSPEN", "REVOG")):
        registrar_evento_session(session, event_type="SITUACAO_RELEVANTE", oportunidade=atual, details={"situacao": atual.situacao})


def avaliar_matching_session(session: Session, *, oportunidade: PncpOportunidade, profile_key: str, cnaes: list[str], mapa_cnae_categoria: dict[str, set[str]], rule_version: str) -> PncpMatching:
    categorias = {item.lower() for valores in mapa_cnae_categoria.values() for item in valores}
    objeto = (oportunidade.objeto or "").lower()
    reasons, missing = [], []
    if not objeto: missing.append("objeto")
    if not cnaes: missing.append("cnaes")
    categorias_cnae = {categoria.lower() for cnae in cnaes for categoria in mapa_cnae_categoria.get(cnae, set())}
    encontradas = sorted(categoria for categoria in categorias_cnae if categoria in objeto)
    if encontradas: reasons.append(f"categorias_compativeis:{','.join(encontradas)}")
    elif not missing: reasons.append("nenhuma_categoria_mapeada_no_objeto")
    compatível = bool(encontradas)
    atual = session.query(PncpMatching).filter_by(source=oportunidade.source, external_id=oportunidade.external_id, profile_key=profile_key, rule_version=rule_version).one_or_none()
    if atual is None:
        atual = PncpMatching(source=oportunidade.source, external_id=oportunidade.external_id, profile_key=profile_key, rule_version=rule_version, compatible=compatível, reasons=reasons, missing_fields=missing)
        session.add(atual)
    else:
        atual.compatible, atual.reasons, atual.missing_fields = compatível, reasons, missing
    return atual
