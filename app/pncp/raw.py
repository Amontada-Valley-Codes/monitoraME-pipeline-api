"""Contrato e repositorio da camada RAW do PNCP.

O modulo nao normaliza nem registra payloads em logs: ele somente conserva uma
versao JSON valida e cria o trabalho que a normalizara.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import IngestionJob, RawPncpRecord


FONTE_PNCP = "PNCP"
JOB_NORMALIZAR = "normalize"
STATUS_PENDENTE = "PENDING"
PRIORIDADE_NORMALIZACAO = 60


class PayloadPncpInvalido(ValueError):
    """Payload que nao pode ser guardado de forma segura em JSONB."""


@dataclass(frozen=True)
class RegistroRawPncp:
    entity_type: str
    external_id: str
    payload: Mapping[str, Any]
    collected_at: datetime
    source_updated_at: datetime | None = None
    run_id: str | None = None
    source: str = FONTE_PNCP


@dataclass(frozen=True)
class ResultadoPersistenciaRaw:
    raw_record_id: int
    payload_hash: str
    raw_criado: bool
    job_id: int
    job_criado: bool


def _payload_canonico(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, Mapping):
        raise PayloadPncpInvalido("payload PNCP deve ser um objeto JSON")
    try:
        serializado = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        # A desserializacao tambem elimina subclasses e garante compatibilidade JSONB.
        valor_json = json.loads(serializado)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PayloadPncpInvalido("payload PNCP nao e JSON valido") from exc
    return valor_json, hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _validar_registro(registro: RegistroRawPncp) -> None:
    if registro.source != FONTE_PNCP:
        raise ValueError("a camada RAW PNCP aceita somente source='PNCP'")
    if not registro.entity_type.strip() or not registro.external_id.strip():
        raise ValueError("entity_type e external_id sao obrigatorios")
    if registro.collected_at.tzinfo is None:
        raise ValueError("collected_at deve incluir timezone")
    if registro.source_updated_at is not None and registro.source_updated_at.tzinfo is None:
        raise ValueError("source_updated_at deve incluir timezone")


def salvar_raw_e_agendar_normalizacao_session(
    session: Session, registro: RegistroRawPncp
) -> ResultadoPersistenciaRaw:
    """Persiste uma versao RAW e seu job usando a transacao ja aberta."""
    _validar_registro(registro)
    payload, payload_hash = _payload_canonico(registro.payload)
    inserir_raw = (
        insert(RawPncpRecord)
        .values(
            source=registro.source, entity_type=registro.entity_type,
            external_id=registro.external_id, collected_at=registro.collected_at,
            source_updated_at=registro.source_updated_at, payload_hash=payload_hash,
            payload=payload, run_id=registro.run_id,
        )
        .on_conflict_do_nothing(constraint="uq_raw_pncp_record_version")
        .returning(RawPncpRecord.id)
    )
    raw_id = session.scalar(inserir_raw)
    raw_criado = raw_id is not None
    if raw_id is None:
        raw_id = session.scalar(
            select(RawPncpRecord.id).where(
                RawPncpRecord.source == registro.source,
                RawPncpRecord.entity_type == registro.entity_type,
                RawPncpRecord.external_id == registro.external_id,
                RawPncpRecord.payload_hash == payload_hash,
            )
        )
    if raw_id is None:  # pragma: no cover - protege contra schema inconsistente
        raise RuntimeError("RAW PNCP nao foi localizado apos conflito")

    dedupe_key = f"normalize:PNCP:{raw_id}"
    if not raw_criado:
        job_id = session.scalar(
            select(IngestionJob.id)
            .where(IngestionJob.raw_record_id == raw_id)
            .order_by(IngestionJob.id.desc())
            .limit(1)
        )
        if job_id is None:  # pragma: no cover - protege contra exclusao manual indevida
            raise RuntimeError("job de normalizacao do RAW existente nao foi localizado")
        return ResultadoPersistenciaRaw(int(raw_id), payload_hash, False, int(job_id), False)

    inserir_job = (
        insert(IngestionJob)
        .values(
            job_type=JOB_NORMALIZAR, entity_type=registro.entity_type,
            source=registro.source, scope_key=str(raw_id), dedupe_key=dedupe_key,
            raw_record_id=raw_id, priority=PRIORIDADE_NORMALIZACAO,
            status=STATUS_PENDENTE, next_run_at=registro.collected_at,
        )
        .on_conflict_do_nothing()
        .returning(IngestionJob.id)
    )
    job_id = session.scalar(inserir_job)
    job_criado = job_id is not None
    if job_id is None:
        job_id = session.scalar(
            select(IngestionJob.id).where(
                IngestionJob.dedupe_key == dedupe_key,
                IngestionJob.status.in_(("PENDING", "RUNNING", "RETRY_WAIT")),
            )
        )
    if job_id is None:  # pragma: no cover - protege contra schema inconsistente
        raise RuntimeError("job de normalizacao nao foi localizado apos conflito")
    return ResultadoPersistenciaRaw(int(raw_id), payload_hash, raw_criado, int(job_id), job_criado)


def salvar_raw_e_agendar_normalizacao(registro: RegistroRawPncp) -> ResultadoPersistenciaRaw:
    """Abre uma transacao no banco principal; RAW e job fazem commit juntos."""
    with orm.main_session() as session:
        return salvar_raw_e_agendar_normalizacao_session(session, registro)


__all__ = [
    "FONTE_PNCP", "JOB_NORMALIZAR", "PayloadPncpInvalido", "RegistroRawPncp",
    "ResultadoPersistenciaRaw", "salvar_raw_e_agendar_normalizacao",
    "salvar_raw_e_agendar_normalizacao_session",
]
