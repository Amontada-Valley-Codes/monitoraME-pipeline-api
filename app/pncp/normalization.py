"""Normalizacao de RAW PNCP sem acesso a rede."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from app.core import orm
from app.core.models import PncpOportunidade, PncpQuarentena, RawPncpRecord
from app.pncp.jobs import JobReivindicado

class ErroNormalizacaoPncp(ValueError): pass

def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str): return None
    try: return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError: return None

def normalizar_payload(raw: RawPncpRecord) -> dict[str, Any]:
    p = raw.payload; externo = p.get("numeroControlePNCP")
    if not isinstance(externo, str) or externo.strip() != raw.external_id:
        raise ErroNormalizacaoPncp("numeroControlePNCP ausente ou divergente")
    return {"source": raw.source, "external_id": externo, "raw_record_id": raw.id, "payload_hash": raw.payload_hash, "source_updated_at": raw.source_updated_at, "orgao": p.get("orgaoEntidade"), "unidade": p.get("unidadeOrgao"), "objeto": p.get("objetoCompra"), "modalidade": p.get("modalidadeNome") or p.get("modalidadeContratacao"), "situacao": p.get("situacaoCompraNome") or p.get("situacaoCompra"), "prazo_proposta_final": _data(p.get("dataEncerramentoProposta")), "url_oficial": p.get("linkSistemaOrigem") or f"https://pncp.gov.br/app/editais/{externo}"}

def normalizar_raw_session(session: Session, raw: RawPncpRecord) -> str:
    dados = normalizar_payload(raw); atual = session.get(PncpOportunidade, {"source": raw.source, "external_id": raw.external_id})
    if atual and atual.payload_hash == raw.payload_hash: return "INALTERADO"
    if atual and atual.source_updated_at and (raw.source_updated_at is None or raw.source_updated_at < atual.source_updated_at): return "ANTIGO"
    if atual is None: session.add(PncpOportunidade(**dados)); return "NOVO"
    for campo, valor in dados.items(): setattr(atual, campo, valor)
    return "ALTERADO"

def normalizar_job(job: JobReivindicado) -> str:
    if job.raw_record_id is None: raise ValueError("job normalize sem raw_record_id")
    with orm.main_session() as session:
        raw = session.get(RawPncpRecord, job.raw_record_id)
        if raw is None: raise ValueError("RAW inexistente")
        try: return normalizar_raw_session(session, raw)
        except ErroNormalizacaoPncp as exc:
            if session.get(PncpQuarentena, raw.id) is None: session.add(PncpQuarentena(raw_record_id=raw.id, motivo=str(exc)))
            raise
