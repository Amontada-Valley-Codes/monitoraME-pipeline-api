"""Worker incremental de embeddings; usa somente o PostgreSQL Analytics."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import socket
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select, exists
from sqlalchemy.dialects.postgresql import insert

from app.core import orm
from app.core.models import IngestionJob, NlpMatchingBatch, NlpProcurementEmbedding, NlpProcurementSegment
from app.nlp.embeddings.generator import gerar_embeddings_segmentos
from app.nlp.embeddings.model import E5EmbeddingModel
from app.nlp.corpus import sincronizar_corpus
from app.nlp.embeddings.queue import (
    agendar_embeddings_pendentes_session,
    contar_embeddings_faltantes_session,
)
from app.nlp.matching.pipeline import processar_batch_enfileirado
from app.pncp.jobs import (
    concluir_job_session,
    falhar_job_session,
    recuperar_leases_expirados_session,
    reivindicar_proximo_job_session,
)

logger = logging.getLogger(__name__)
JOB_TYPE = "nlp_embedding"
MATCHING_JOB_TYPE = "nlp_matching_batch"


def existe_batch_matching_pronto_session(session: Any) -> bool:
    """Informa se há batch de matching aguardando execução."""
    return bool(session.scalar(select(exists().where(
        IngestionJob.job_type == MATCHING_JOB_TYPE,
        IngestionJob.status.in_(("PENDING", "RETRY_WAIT")),
        IngestionJob.next_run_at <= datetime.now(timezone.utc),
    ))))

def resumir_job(job: Any) -> dict[str, Any]:
    """Extrai identidade operacional segura para logs, sem texto do corpus."""
    if job.job_type == MATCHING_JOB_TYPE:
        return {"batch_id": job.scope_key, "job_type": job.job_type}
    try:
        identidade = json.loads(job.scope_key)
    except (TypeError, json.JSONDecodeError):
        return {"job_type": job.job_type, "scope_key_valid": False}
    if not isinstance(identidade, dict):
        return {"job_type": job.job_type, "scope_key_valid": False}
    return {
        "job_type": job.job_type,
        "source": identidade.get("source"),
        "external_id": identidade.get("external_id"),
        "segment_type": identidade.get("segment_type"),
        "segment_key": identidade.get("segment_key"),
        "model_version": identidade.get("model_version"),
    }


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
        # Revalida apÃ³s inferÃªncia para impedir persistÃªncia de vetor obsoleto.
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


def executar_worker(
    *, idle_seconds: float | None = None, stop_after: int | None = None,
    drain: bool = False, idle_exit_seconds: float = 5.0,
) -> None:
    idle_seconds = idle_seconds if idle_seconds is not None else float(os.getenv("NLP_EMBEDDING_IDLE_SECONDS", "10"))
    model = E5EmbeddingModel(
        batch_size=1,
        device=os.getenv("NLP_EMBEDDING_DEVICE", "cpu"),
    )
    worker_id = f"matching-nlp:{socket.gethostname()}:{uuid4().hex[:8]}"
    processados = 0
    embeddings_faltantes_anterior: int | None = None
    modelo_carregado = False
    ultimo_trabalho = time.monotonic()
    ultimo_recovery = 0.0
    corpus_bootstrap_concluido = False
    logger.info("Worker NLP iniciado", extra={"worker_id": worker_id, "model_version": model.model_version, "device": model.device})
    while stop_after is None or processados < stop_after:
        try:
            with orm.main_session() as session:
                recuperados = 0
                if time.monotonic() - ultimo_recovery >= 30:
                    recuperados = recuperar_leases_expirados_session(session, job_type=JOB_TYPE)
                    recuperados += recuperar_leases_expirados_session(session, job_type=MATCHING_JOB_TYPE)
                    ultimo_recovery = time.monotonic()
                agendados = 0
                job = None
                embeddings_faltantes = 0
                if not corpus_bootstrap_concluido:
                    resumo = sincronizar_corpus(session, page_size=200)
                    corpus_bootstrap_concluido = True
                    logger.info("Corpus de oportunidades sincronizado para embeddings", extra={
                        "opportunities_scanned": resumo.opportunities_scanned,
                        "segments_created": resumo.segments_created,
                        "segments_without_text": resumo.segments_without_text,
                        "embedding_jobs_scheduled": resumo.embedding_jobs_scheduled,
                    })
                agendados = agendar_embeddings_pendentes_session(
                    session, model_version=model.model_version
                )
                job = reivindicar_proximo_job_session(
                    session, worker_id=worker_id, lease_seconds=1800, job_type=JOB_TYPE
                )
                matching_pendente = existe_batch_matching_pronto_session(session)
                if matching_pendente:
                    embeddings_faltantes = contar_embeddings_faltantes_session(
                        session, model_version=model.model_version,
                    )
                    if job is None and embeddings_faltantes == 0:
                        job = reivindicar_proximo_job_session(
                            session, worker_id=worker_id, lease_seconds=7200,
                            job_type=MATCHING_JOB_TYPE,
                        )
            if recuperados or agendados:
                logger.info("Trabalho NLP preparado na fila", extra={"leases_recuperados": recuperados, "jobs_embeddings_agendados": agendados, "model_version": model.model_version})
            if embeddings_faltantes and embeddings_faltantes != embeddings_faltantes_anterior:
                logger.warning("Batch de matching bloqueado por embeddings de oportunidades pendentes", extra={"embeddings_faltantes": embeddings_faltantes, "model_version": model.model_version})
            elif matching_pendente and embeddings_faltantes_anterior and embeddings_faltantes == 0:
                logger.info("Embeddings de oportunidades completos; batches de matching liberados", extra={"model_version": model.model_version})
            embeddings_faltantes_anterior = embeddings_faltantes
            if job is None:
                unload_after = float(os.getenv("NLP_MODEL_IDLE_UNLOAD_SECONDS", "60"))
                if (
                    modelo_carregado
                    and unload_after > 0
                    and time.monotonic() - ultimo_trabalho >= unload_after
                ):
                    model.unload()
                    modelo_carregado = False
                    logger.info("Modelo de embeddings liberado por ociosidade", extra={
                        "model_version": model.model_version,
                        "idle_seconds": unload_after,
                    })
                if drain and time.monotonic() - ultimo_trabalho >= idle_exit_seconds:
                    break
                time.sleep(idle_seconds)
                continue
            ultimo_trabalho = time.monotonic()
            try:
                contexto_job = resumir_job(job)
                logger.info("Job NLP reivindicado para processamento", extra={
                    "job_id": job.id, "attempt": job.attempts, "worker_id": worker_id,
                    **contexto_job,
                })
                if not modelo_carregado:
                    logger.info("Carregando modelo de embeddings sob demanda", extra={
                        "model_version": model.model_version, "device": model.device,
                    })
                    model.load()
                    modelo_carregado = True
                if job.job_type == MATCHING_JOB_TYPE:
                    resultado, _ = processar_batch_enfileirado(job, model=model)
                    detalhes = {"batch_id": job.scope_key, "results": len(resultado), "job_type": job.job_type}
                else:
                    resultado = processar_job(job, model)
                    detalhes = {"resultado": resultado, **contexto_job}
                processados += 1
                logger.info("Job NLP concluido", extra={"job_id": job.id, **detalhes, "attempt": job.attempts, "worker_id": worker_id})
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
                logger.exception("Falha em job NLP", extra={"job_id": job.id, "status": status, "worker_id": worker_id, **contexto_job})
                processados += 1
        except Exception:
            logger.exception("Falha no ciclo do worker de embeddings")
            time.sleep(idle_seconds)
    logger.info("Worker de embeddings finalizado", extra={"jobs_processados": processados})
