"""Agendamento duravel de vetores para segmentos PNCP preparados."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from sqlalchemy import exists, func, not_, select, tuple_
from sqlalchemy.orm import Session

from app.core.models import IngestionJob, NlpProcurementEmbedding, NlpProcurementSegment
from app.nlp.embeddings.model import E5EmbeddingModel
from app.pncp.jobs import NovoJob, agendar_job_session


JOB_TYPE = "nlp_embedding"
JOB_ENTITY = "PROCUREMENT_SEGMENT"
JOB_SOURCE = "PNCP"


def _identidade(segmento: NlpProcurementSegment, model_version: str) -> dict[str, str]:
    return {
        "source": segmento.source,
        "external_id": segmento.external_id,
        "segment_type": segmento.segment_type,
        "segment_key": segmento.segment_key,
        "text_hash": str(segmento.text_hash),
        "model_version": model_version,
    }


def _dedupe_key(identidade: dict[str, str]) -> str:
    canonical = json.dumps(identidade, sort_keys=True, separators=(",", ":"))
    return "nlp-embedding:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _agendar_segmentos_session(
    session: Session,
    *,
    segmentos: list[NlpProcurementSegment],
    model_version: str,
    max_new_jobs: int | None = None,
) -> int:
    if not segmentos:
        return 0
    identidades = [_identidade(segmento, model_version) for segmento in segmentos]
    dedupes = {_dedupe_key(identidade) for identidade in identidades}
    existentes = set(session.scalars(
        select(IngestionJob.dedupe_key).where(IngestionJob.dedupe_key.in_(dedupes))
    ))
    agendados = 0
    for segmento, identidade in zip(segmentos, identidades, strict=True):
        dedupe = _dedupe_key(identidade)
        if dedupe in existentes:
            continue
        _, criado = agendar_job_session(session, NovoJob(
            job_type=JOB_TYPE,
            entity_type=JOB_ENTITY,
            source=JOB_SOURCE,
            scope_key=json.dumps(identidade, sort_keys=True, separators=(",", ":")),
            dedupe_key=dedupe,
            priority=70,
            next_run_at=datetime.now(timezone.utc),
            max_attempts=int(os.getenv("NLP_EMBEDDING_MAX_ATTEMPTS", "5")),
        ))
        agendados += int(criado)
        if max_new_jobs is not None and agendados >= max_new_jobs:
            break
    return agendados


def agendar_embeddings_oportunidades_session(
    session: Session,
    *,
    opportunity_keys: list[tuple[str, str]],
    model_version: str | None = None,
) -> int:
    """Enfileira segmentos READY da oportunidade na mesma transacao do corpus."""
    if not opportunity_keys:
        return 0
    embeddings = NlpProcurementEmbedding
    versao = model_version or E5EmbeddingModel().model_version
    tem_embedding = exists(select(1).where(
        embeddings.source == NlpProcurementSegment.source,
        embeddings.external_id == NlpProcurementSegment.external_id,
        embeddings.segment_type == NlpProcurementSegment.segment_type,
        embeddings.segment_key == NlpProcurementSegment.segment_key,
        embeddings.text_hash == NlpProcurementSegment.text_hash,
        embeddings.model_version == versao,
    ))
    segmentos = list(session.scalars(
        select(NlpProcurementSegment).where(
            tuple_(NlpProcurementSegment.source, NlpProcurementSegment.external_id).in_(opportunity_keys),
            NlpProcurementSegment.status == "READY",
            NlpProcurementSegment.text_hash.is_not(None),
            not_(tem_embedding),
        ).order_by(
            NlpProcurementSegment.source,
            NlpProcurementSegment.external_id,
            NlpProcurementSegment.segment_type,
            NlpProcurementSegment.segment_key,
        )
    ))
    return _agendar_segmentos_session(
        session,
        segmentos=segmentos,
        model_version=versao,
    )


def agendar_embeddings_pendentes_session(
    session: Session,
    *,
    model_version: str,
    limit: int = 100,
) -> int:
    """Agenda um lote do corpus legado que ainda não tem embedding."""
    if limit < 1:
        raise ValueError("limit deve ser positivo")
    # Bootstrap legado em blocos: evita revisitar o corpus enquanto o lote
    # anterior ainda está sendo drenado. Segmentos novos entram diretamente
    # pela fila transacional de sincronizar_corpus.
    fila_ativa = session.scalar(
        select(IngestionJob.id)
        .where(IngestionJob.job_type == JOB_TYPE, IngestionJob.status.in_(("PENDING", "RUNNING", "RETRY_WAIT")))
        .limit(1)
    )
    if fila_ativa is not None:
        return 0
    embeddings = NlpProcurementEmbedding
    tem_embedding = exists(select(1).where(
        embeddings.source == NlpProcurementSegment.source,
        embeddings.external_id == NlpProcurementSegment.external_id,
        embeddings.segment_type == NlpProcurementSegment.segment_type,
        embeddings.segment_key == NlpProcurementSegment.segment_key,
        embeddings.text_hash == NlpProcurementSegment.text_hash,
        embeddings.model_version == model_version,
    ))
    candidatos = list(session.scalars(
        select(NlpProcurementSegment)
        .where(
            NlpProcurementSegment.status == "READY",
            NlpProcurementSegment.text_hash.is_not(None),
            not_(tem_embedding),
        )
        .order_by(NlpProcurementSegment.prepared_at.asc())
        .limit(max(limit * 5, limit))
    ))
    return _agendar_segmentos_session(
        session,
        segmentos=candidatos,
        model_version=model_version,
        max_new_jobs=limit,
    )


def contar_embeddings_faltantes_session(session: Session, *, model_version: str) -> int:
    embeddings = NlpProcurementEmbedding
    tem_embedding = exists(select(1).where(
        embeddings.source == NlpProcurementSegment.source,
        embeddings.external_id == NlpProcurementSegment.external_id,
        embeddings.segment_type == NlpProcurementSegment.segment_type,
        embeddings.segment_key == NlpProcurementSegment.segment_key,
        embeddings.text_hash == NlpProcurementSegment.text_hash,
        embeddings.model_version == model_version,
    ))
    return int(session.scalar(
        select(func.count()).select_from(NlpProcurementSegment).where(
            NlpProcurementSegment.status == "READY",
            NlpProcurementSegment.text_hash.is_not(None),
            not_(tem_embedding),
        )
    ) or 0)


def diagnosticar_embeddings_faltantes_session(
    session: Session, *, model_version: str,
) -> dict[str, object]:
    """Resume faltantes, segmentos afetados e estado geral da fila NLP."""
    embeddings = NlpProcurementEmbedding
    tem_embedding = exists(select(1).where(
        embeddings.source == NlpProcurementSegment.source,
        embeddings.external_id == NlpProcurementSegment.external_id,
        embeddings.segment_type == NlpProcurementSegment.segment_type,
        embeddings.segment_key == NlpProcurementSegment.segment_key,
        embeddings.text_hash == NlpProcurementSegment.text_hash,
        embeddings.model_version == model_version,
    ))
    faltantes = (
        select(
            NlpProcurementSegment.segment_type,
            func.count().label("count"),
            func.min(NlpProcurementSegment.prepared_at).label("oldest_prepared_at"),
        )
        .where(
            NlpProcurementSegment.status == "READY",
            NlpProcurementSegment.text_hash.is_not(None),
            not_(tem_embedding),
        )
        .group_by(NlpProcurementSegment.segment_type)
    )
    por_tipo = {
        str(tipo): {"count": int(count), "oldest_prepared_at": oldest.isoformat() if oldest else None}
        for tipo, count, oldest in session.execute(faltantes)
    }
    fila = dict(session.execute(
        select(IngestionJob.status, func.count())
        .where(IngestionJob.job_type == JOB_TYPE)
        .group_by(IngestionJob.status)
    ).all())
    return {
        "embeddings_faltantes": sum(item["count"] for item in por_tipo.values()),
        "faltantes_por_segment_type": por_tipo,
        "jobs_embeddings_por_status": {str(status): int(count) for status, count in fila.items()},
    }


__all__ = [
    "agendar_embeddings_oportunidades_session",
    "agendar_embeddings_pendentes_session",
    "contar_embeddings_faltantes_session",
    "diagnosticar_embeddings_faltantes_session",
]
