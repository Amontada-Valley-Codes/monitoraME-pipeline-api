"""Fluxo independente de atualizacoes globais do PNCP."""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core import orm
from app.core.models import PncpContratacaoAtual, PncpCursor
from app.pncp.ingestion import ClientePncp, PncpColetaError
from app.pncp.jobs import JobReivindicado, NovoJob
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session
from app.pncp.checkpoints import ChaveCursor, confirmar_janela

FLOW = "atualizacao"

def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str): return None
    try: return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError: return None

def agendar_atualizacao(*, inicio: datetime, fim: datetime, modalidades: list[int]) -> list[NovoJob]:
    return [NovoJob("incremental", "CONTRATACAO", "PNCP", f"atualizacao:{m}", f"atualizacao:{inicio.date()}:{fim.date()}:{m}", 80, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1) for m in modalidades]

def salvar_versao_atual_session(session: Session, item: dict[str, Any], payload_hash: str, source_updated_at: datetime | None) -> str:
    external_id = str(item["numeroControlePNCP"])
    anterior = session.get(PncpContratacaoAtual, {"source": "PNCP", "external_id": external_id})
    if anterior and anterior.payload_hash == payload_hash: return "INALTERADO"
    if anterior and anterior.source_updated_at and (source_updated_at is None or source_updated_at < anterior.source_updated_at): return "ANTIGO"
    session.execute(insert(PncpContratacaoAtual).values(source="PNCP", external_id=external_id, payload_hash=payload_hash, source_updated_at=source_updated_at, payload=item).on_conflict_do_update(index_elements=["source", "external_id"], set_={"payload_hash": payload_hash, "source_updated_at": source_updated_at, "payload": item, "updated_at": text("now()")}))
    return "NOVO" if anterior is None else "ALTERADO"

def coletar_atualizacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 100) -> dict[str, int]:
    if job.window_start is None or job.window_end is None: raise ValueError("janela obrigatoria")
    modalidade = int(job.scope_key.rsplit(":", 1)[1]); pagina = job.page or 1; totais = {"NOVO": 0, "ALTERADO": 0, "INALTERADO": 0}
    while True:
        resposta = cliente.buscar_atualizacoes(inicio=job.window_start.date(), fim=job.window_end.date(), modalidade=modalidade, pagina=pagina, tamanho_pagina=tamanho_pagina)
        if not resposta.itens and resposta.total_paginas and pagina < resposta.total_paginas: raise PncpColetaError("pagina de atualizacao incompleta")
        for item in resposta.itens:
            if not isinstance(item.get("numeroControlePNCP"), str): raise PncpColetaError("atualizacao sem numeroControlePNCP")
            atualizado = _data(item.get("dataAtualizacao"))
            with orm.main_session() as s:
                raw = salvar_raw_e_agendar_normalizacao_session(s, RegistroRawPncp("CONTRATACAO", item["numeroControlePNCP"], item, datetime.now(timezone.utc), atualizado, f"job:{job.id}:pagina:{pagina}"))
                classe = salvar_versao_atual_session(s, item, raw.payload_hash, atualizado)
            if classe != "ANTIGO": totais[classe] += 1
        if resposta.total_paginas is not None and pagina >= resposta.total_paginas: break
        if resposta.total_paginas is None and len(resposta.itens) < tamanho_pagina: break
        pagina += 1
    confirmar_janela(ChaveCursor("PNCP", "CONTRATACAO", FLOW, f"modalidade:{modalidade}"), final=job.window_end, run_id=str(job.id))
    return totais
