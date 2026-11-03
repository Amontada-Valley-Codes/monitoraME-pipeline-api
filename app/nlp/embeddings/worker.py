"""Worker incremental de embeddings; usa somente o PostgreSQL Analytics."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import socket
import time
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select, not_, exists, func
from sqlalchemy.dialects.postgresql import insert

from app.core import orm
from app.core.models import IngestionJob, NlpMatchingBatch, NlpProcurementEmbedding, NlpProcurementSegment
from app.nlp.embeddings.generator import gerar_embeddings_segmentos
from app.nlp.embeddings.model import E5EmbeddingModel
from app.nlp.corpus import sincronizar_corpus
from app.nlp.matching.pipeline import processar_batch_enfileirado
from app.pncp.jobs import (
    NovoJob,
    agendar_job_session,
    concluir_job_session,
    falhar_job_session,
    recuperar_leases_expirados_session,
    reivindicar_proximo_job_session,
)

logger = logging.getLogger(__name__)
JOB_TYPE = "nlp_embedding"
MATCHING_JOB_TYPE = "nlp_matching_batch"
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
    return "nlp-embedding:" + sha256(canonical.encode("utf-8")).hexdigest()


def agendar_embeddings_pendentes_session(session: Any, *, model_version: str, limit: int = 100) -> int:
    """Agenda apenas hashes ainda sem vetor e sem job terminal correspondente."""
    embeddings = NlpProcurementEmbedding
    jobs = IngestionJob
    # Enquanto houver trabalho ativo, deixe os workers drenarem a fila atual.
    # Sem este fast path, cada embedding concluído revarria todos os segmentos
    # sem vetor e fazia uma consulta SQL por candidato para verificar dedupe.
    fila_ativa = session.scalar(
        select(jobs.id)
        .where(jobs.job_type == JOB_TYPE, jobs.status.in_(("PENDING", "RUNNING", "RETRY_WAIT")))
        .limit(1)
    )
    if fila_ativa is not None:
        return 0
    tem_embedding = exists(select(1).where(
        embeddings.source == NlpProcurementSegment.source,
        embeddings.external_id == NlpProcurementSegment.external_id,
        embeddings.segment_type == NlpProcurementSegment.segment_type,
        embeddings.segment_key == NlpProcurementSegment.segment_key,
        embeddings.text_hash == NlpProcurementSegment.text_hash,
        embeddings.model_version == model_version,
    ))
    candidatos = session.scalars(
        select(NlpProcurementSegment)
        .where(NlpProcurementSegment.status == "READY", NlpProcurementSegment.text_hash.is_not(None), not_(tem_embedding))
        .order_by(NlpProcurementSegment.prepared_at.asc())
        .execution_options(yield_per=250)
    )
    agendados = 0
    for segmento in candidatos:
        identidade = _identidade(segmento, model_version)
        dedupe = _dedupe_key(identidade)
        terminal_ou_ativo = session.scalar(select(jobs.id).where(jobs.dedupe_key == dedupe).limit(1))
        if terminal_ou_ativo is not None:
            continue
        _, novo = agendar_job_session(session, NovoJob(
            job_type=JOB_TYPE,
            entity_type=JOB_ENTITY,
            source=JOB_SOURCE,
            scope_key=json.dumps(identidade, sort_keys=True, separators=(",", ":")),
            dedupe_key=dedupe,
            priority=70,
            next_run_at=datetime.now(timezone.utc),
            max_attempts=int(os.getenv("NLP_EMBEDDING_MAX_ATTEMPTS", "5")),
        ))
        agendados += int(novo)
        if agendados >= limit:
            break
    return agendados


def contar_embeddings_faltantes_session(session: Any, *, model_version: str) -> int:
    """Conta segmentos READY sem vetor da revisão atual do modelo."""
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
        select(func.count()).select_from(NlpProcurementSegment)
        .where(
            NlpProcurementSegment.status == "READY",
            NlpProcurementSegment.text_hash.is_not(None),
            not_(tem_embedding),
        )
    ) or 0)


def processar_job(job: Any, model: E5EmbeddingModel) -> str:
    identidade = json.loads(job.scope_key)
    if not isinstance(identidade, dict) or set(identidade) != {
        "source", "external_id", "segment_type", "segment_key", "text_hash", "model_version"
    }:
        raise ValueError("job NLP possui identidade invalida")
    if identidade["model_version"] != model.model_version:
        raise ValueError("job NLP aponta para versao de modelo diferente do worker")

    with orm.main_session() as session:
        segmento = session.get(NlpProcurementSegment, (
            identidade["source"], identidade["external_id"], identidade["segment_type"], identidade["segment_key"]
        ))
        texto = segmento.normalized_text if segmento and segmento.text_hash == identidade["text_hash"] else None
    if texto is None:
        return "stale"

    lote = gerar_embeddings_segmentos(model, [texto])
    vetor = lote.vectors[0]
    with orm.main_session() as session:
        # Revalida após inferência para impedir persistência de vetor obsoleto.
        segmento_atual = session.get(NlpProcurementSegment, (
            identidade["source"], identidade["external_id"], identidade["segment_type"], identidade["segment_key"]
        ))
        if segmento_atual is not None and segmento_atual.text_hash == identidade["text_hash"]:
            tabela = NlpProcurementEmbedding.__table__
            comando = insert(tabela).values(**identidade, dimensions=len(vetor), vector=vetor)
            comando = comando.on_conflict_do_update(
                index_elements=["source", "external_id", "segment_type", "segment_key", "text_hash", "model_version"],
                set_={"dimensions": comando.excluded.dimensions, "vector": comando.excluded.vector},
            )
            session.execute(comando)
            status = "stored"
        else:
            status = "stale"
        if not concluir_job_session(session, job_id=job.id, lease_token=job.lease_token):
            raise RuntimeError("lease perdido antes de concluir job de embedding")
    return status


def executar_worker(*, idle_seconds: float | None = None, stop_after: int | None = None) -> None:
    idle_seconds = idle_seconds if idle_seconds is not None else float(os.getenv("NLP_EMBEDDING_IDLE_SECONDS", "10"))
    model = E5EmbeddingModel(
        batch_size=1,
        device=os.getenv("NLP_EMBEDDING_DEVICE", "cpu"),
    )
    with orm.main_session() as session:
        segmentos_existentes = session.scalar(select(func.count()).select_from(NlpProcurementSegment)) or 0
        if segmentos_existentes == 0:
            resumo = sincronizar_corpus(session, page_size=200)
            logger.info("Corpus NLP inicial preparado", extra={
                "opportunities_scanned": resumo.opportunities_scanned,
                "segments_created": resumo.segments_created,
                "segments_without_text": resumo.segments_without_text,
            })
    logger.info("Carregando modelo de embeddings", extra={"model_version": model.model_version, "device": model.device})
    model.load()
    worker_id = f"matching-nlp:{socket.gethostname()}:{uuid4().hex[:8]}"
    processados = 0
    embeddings_faltantes_anterior: int | None = None
    logger.info("Worker de embeddings iniciado", extra={"worker_id": worker_id, "model_version": model.model_version})
    while stop_after is None or processados < stop_after:
        try:
            with orm.main_session() as session:
                recuperados = recuperar_leases_expirados_session(session, job_type=JOB_TYPE)
                recuperados += recuperar_leases_expirados_session(session, job_type=MATCHING_JOB_TYPE)
                agendados = agendar_embeddings_pendentes_session(
                    session, model_version=model.model_version
                )
                job = reivindicar_proximo_job_session(
                    session, worker_id=worker_id, lease_seconds=1800, job_type=JOB_TYPE
                )
                embeddings_faltantes = contar_embeddings_faltantes_session(
                    session, model_version=model.model_version,
                )
                if job is None and embeddings_faltantes == 0:
                    job = reivindicar_proximo_job_session(
                        session, worker_id=worker_id, lease_seconds=7200,
                        job_type=MATCHING_JOB_TYPE,
                    )
            if recuperados or agendados:
                logger.info("Fila NLP atualizada", extra={"leases_recuperados": recuperados, "jobs_agendados": agendados})
            if embeddings_faltantes and embeddings_faltantes != embeddings_faltantes_anterior:
                logger.warning("Matching aguardando vetores completos", extra={"embeddings_faltantes": embeddings_faltantes})
            embeddings_faltantes_anterior = embeddings_faltantes
            if job is None:
                time.sleep(idle_seconds)
                continue
            try:
                if job.job_type == MATCHING_JOB_TYPE:
                    resultado, _ = processar_batch_enfileirado(job, model=model)
                    detalhes = {"batch_id": job.scope_key, "results": len(resultado)}
                else:
                    resultado = processar_job(job, model)
                    detalhes = {"resultado": resultado}
                processados += 1
                logger.info("Job NLP concluido", extra={"job_id": job.id, **detalhes, "attempt": job.attempts})
            except Exception as exc:
                with orm.main_session() as session:
                    status = falhar_job_session(
                        session,
                        job=job,
                        erro=f"{type(exc).__name__}: {str(exc)[:500]}",
                        retryable=True,
                        contexto={"component": "matching_nlp_worker", "job_type": job.job_type},
                    )
                    if status is not None and job.job_type == MATCHING_JOB_TYPE:
                        batch = session.get(NlpMatchingBatch, job.scope_key)
                        if batch is not None:
                            batch.status = "PENDING" if status == "RETRY_WAIT" else "FAILED"
                            if status == "FAILED":
                                batch.failed_companies = batch.total_companies - batch.processed_companies
                            batch.updated_at = datetime.now(timezone.utc)
                logger.exception("Falha em job NLP", extra={"job_id": job.id, "status": status, "job_type": job.job_type})
                processados += 1
        except Exception:
            logger.exception("Falha no ciclo do worker de embeddings")
            time.sleep(idle_seconds)
    logger.info("Worker de embeddings finalizado", extra={"jobs_processados": processados})
