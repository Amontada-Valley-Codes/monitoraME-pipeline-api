"""Planejamento e confirmacao de watermarks, independentes do coletor."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from sqlalchemy.orm import Session
from app.core import orm
from app.core.models import PncpSyncCursor

OVERLAP_PADRAO = timedelta(minutes=int(os.getenv("PNCP_OVERLAP_MINUTES", "5")))

@dataclass(frozen=True)
class KeyCursor:
    source: str
    entity_type: str
    flow: str
    scope_key: str


ChaveCursor = KeyCursor

def proxima_janela(
    key: KeyCursor,
    *,
    now: datetime | None = None,
    size: timedelta | None = None,
    agora: datetime | None = None,
    tamanho: timedelta | None = None,
    last_confirmed_end: datetime | None,
    overlap: timedelta = OVERLAP_PADRAO,
) -> tuple[datetime, datetime]:
    """Calcula a janela usando nomes em ingles ou o contrato publico em portugues."""
    if now is not None and agora is not None or size is not None and tamanho is not None:
        raise ValueError("informe somente now/agora e size/tamanho")
    now = now or agora
    size = size or tamanho
    if now is None or size is None:
        raise ValueError("now/agora e size/tamanho sao obrigatorios")
    if now.tzinfo is None or size <= timedelta() or overlap < timedelta(): raise ValueError("datas timezone-aware e duracoes validas sao obrigatorias")
    inicio = (last_confirmed_end - overlap) if last_confirmed_end else now - size
    return inicio, now

def confirmar_janela_session(session: Session, key: KeyCursor, *, final: datetime, run_id: str) -> None:
    """Chamar somente depois de todas as paginas RAW estarem persistidas."""
    cursor = session.get(PncpSyncCursor, {"source": key.source, "entity_type": key.entity_type, "flow": key.flow, "scope_key": key.scope_key})
    if cursor is None:
        session.add(PncpSyncCursor(source=key.source, entity_type=key.entity_type, flow=key.flow, scope_key=key.scope_key, last_confirmed_end=final, last_run_id=run_id, status="CONFIRMED"))
    elif cursor.last_confirmed_end is None or final >= cursor.last_confirmed_end:
        cursor.last_confirmed_end, cursor.last_run_id, cursor.status = final, run_id, "CONFIRMED"

def confirmar_janela(key: KeyCursor, *, final: datetime, run_id: str) -> None:
    with orm.main_session() as session: confirmar_janela_session(session, key, final=final, run_id=run_id)


__all__ = ["KeyCursor", "ChaveCursor", "OVERLAP_PADRAO", "proxima_janela", "confirmar_janela", "confirmar_janela_session"]
