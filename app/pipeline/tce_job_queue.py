"""Fila persistente dos jobs TCE, isolada dos handlers de ingestao."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import random
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.core import orm
from app.core.config import TCE_JOB_MAX_ATTEMPTS
from app.core.models import IngestionJob, IngestionJobEvent


logger = logging.getLogger(__name__)

SOURCE = "TCE-CE"
PENDING = "PENDING"
RUNNING = "RUNNING"
RETRY_WAIT = "RETRY_WAIT"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
ACTIVE_STATUSES = (PENDING, RUNNING, RETRY_WAIT)


@dataclass(frozen=True)
class NovoJobTce:
    job_type: str
    entity_type: str
    scope_key: str
    dedupe_key: str
    priority: int
    next_run_at: datetime
    max_attempts: int = TCE_JOB_MAX_ATTEMPTS
    window_start: datetime | None = None
    window_end: datetime | None = None
    page: int | None = None


@dataclass(frozen=True)
class JobTceReivindicado:
    id: int
    job_type: str
    entity_type: str
    scope_key: str
    dedupe_key: str
    attempts: int
    max_attempts: int
    lease_token: str
    window_start: datetime | None
    window_end: datetime | None
    page: int | None


def _evento(
    session: Session,
    *,
    job_id: int,
    event_type: str,
    attempt: int | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        IngestionJobEvent(
            job_id=job_id,
            event_type=event_type,
            attempt=attempt,
            details=details or {},
        )
    )


def _validar_job(job: NovoJobTce) -> None:
    if not all(
        (
            job.job_type.startswith("tce_"),
            job.entity_type.strip(),
            job.scope_key.strip(),
            job.dedupe_key.strip(),
        )
    ):
        raise ValueError("job TCE deve informar tipo, entidade, escopo e dedupe_key")
    if job.next_run_at.tzinfo is None:
        raise ValueError("next_run_at deve incluir timezone")
    if job.max_attempts < 1:
        raise ValueError("max_attempts deve ser maior que zero")


def _valores_job(job: NovoJobTce) -> dict[str, object]:
    _validar_job(job)
    return {
        "job_type": job.job_type,
        "entity_type": job.entity_type,
        "source": SOURCE,
        "scope_key": job.scope_key,
        "dedupe_key": job.dedupe_key,
        "priority": job.priority,
        "status": PENDING,
        "next_run_at": job.next_run_at,
        "max_attempts": job.max_attempts,
        "window_start": job.window_start,
        "window_end": job.window_end,
        "page": job.page,
        "raw_record_id": None,
    }


def agendar_job_unico_session(
    session: Session,
    job: NovoJobTce,
) -> tuple[int, bool]:
    """Cria uma unica ocorrencia historica para a dedupe_key informada."""
    _validar_job(job)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:chave))"),
        {"chave": job.dedupe_key},
    )
    existente = session.scalar(
        select(IngestionJob.id).where(
            IngestionJob.source == SOURCE,
            IngestionJob.dedupe_key == job.dedupe_key,
        ).order_by(IngestionJob.id).limit(1)
    )
    if existente is not None:
        return int(existente), False
    modelo = IngestionJob(**_valores_job(job))
    session.add(modelo)
    session.flush()
    _evento(session, job_id=int(modelo.id), event_type="SCHEDULED")
    return int(modelo.id), True


def agendar_job_unico(job: NovoJobTce) -> tuple[int, bool]:
    with orm.main_session() as session:
        return agendar_job_unico_session(session, job)


def agendar_lote_session(
    session: Session,
    *,
    lote_id: str,
    jobs: Iterable[NovoJobTce],
) -> tuple[int, int]:
    """Insere filhos do lote atomicamente e tolera retry do job de catalogo."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:chave))"),
        {"chave": f"tce-lote:{lote_id}"},
    )
    prefixo = f"tce:{lote_id}:%"
    existentes = set(
        session.scalars(
            select(IngestionJob.dedupe_key).where(
                IngestionJob.source == SOURCE,
                IngestionJob.dedupe_key.like(prefixo),
            )
        ).all()
    )
    valores = []
    avaliados = 0
    for job in jobs:
        avaliados += 1
        if job.dedupe_key not in existentes:
            valores.append(_valores_job(job))
            existentes.add(job.dedupe_key)
    for inicio in range(0, len(valores), 1000):
        session.execute(insert(IngestionJob), valores[inicio : inicio + 1000])
    return len(valores), avaliados


_SQL_REIVINDICAR = text("""
WITH candidate AS (
    SELECT id
    FROM ingestion_jobs
    WHERE source = 'TCE-CE'
      AND left(job_type, 4) = 'tce_'
      AND status IN ('PENDING', 'RETRY_WAIT')
      AND next_run_at <= now()
      AND attempts < max_attempts
    ORDER BY priority DESC, next_run_at ASC, created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE ingestion_jobs AS job
SET status = 'RUNNING', locked_by = :worker_id, lease_token = :lease_token,
    lease_until = now() + (:lease_seconds * interval '1 second'),
    heartbeat_at = now(), attempts = job.attempts + 1, updated_at = now()
FROM candidate
WHERE job.id = candidate.id
RETURNING job.*
""")


def reivindicar_proximo_job_session(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> JobTceReivindicado | None:
    if not worker_id.strip() or lease_seconds < 1:
        raise ValueError("worker_id e lease_seconds validos sao obrigatorios")
    registro = session.execute(
        _SQL_REIVINDICAR,
        {
            "worker_id": worker_id,
            "lease_token": str(uuid4()),
            "lease_seconds": lease_seconds,
        },
    ).mappings().first()
    if registro is None:
        return None
    job = JobTceReivindicado(
        id=int(registro["id"]),
        job_type=str(registro["job_type"]),
        entity_type=str(registro["entity_type"]),
        scope_key=str(registro["scope_key"]),
        dedupe_key=str(registro["dedupe_key"]),
        attempts=int(registro["attempts"]),
        max_attempts=int(registro["max_attempts"]),
        lease_token=str(registro["lease_token"]),
        window_start=registro["window_start"],
        window_end=registro["window_end"],
        page=registro["page"],
    )
    _evento(
        session,
        job_id=job.id,
        event_type="STARTED",
        attempt=job.attempts,
    )
    return job


def reivindicar_proximo_job(
    *,
    worker_id: str,
    lease_seconds: int,
) -> JobTceReivindicado | None:
    with orm.main_session() as session:
        return reivindicar_proximo_job_session(
            session,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )


def renovar_lease_session(
    session: Session,
    *,
    job_id: int,
    lease_token: str,
    lease_seconds: int,
) -> bool:
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs
        SET lease_until = now() + (:lease_seconds * interval '1 second'),
            heartbeat_at = now(), updated_at = now()
        WHERE id = :job_id AND source = 'TCE-CE' AND status = 'RUNNING'
          AND lease_token = :lease_token
        """),
        {
            "job_id": job_id,
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
        },
    )
    return resultado.rowcount == 1


def renovar_lease(
    *,
    job_id: int,
    lease_token: str,
    lease_seconds: int,
) -> bool:
    with orm.main_session() as session:
        return renovar_lease_session(
            session,
            job_id=job_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )


def concluir_job_session(
    session: Session,
    *,
    job_id: int,
    lease_token: str,
) -> bool:
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs
        SET status = 'SUCCEEDED', locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, last_error = NULL,
            last_error_context = NULL,
            success_count = success_count + 1, updated_at = now()
        WHERE id = :job_id AND source = 'TCE-CE' AND status = 'RUNNING'
          AND lease_token = :lease_token
        """),
        {"job_id": job_id, "lease_token": lease_token},
    )
    if resultado.rowcount == 1:
        _evento(session, job_id=job_id, event_type=SUCCEEDED)
        return True
    return False


_SQL_FALHAR = text("""
UPDATE ingestion_jobs
SET status = :status, locked_by = NULL, lease_token = NULL,
    lease_until = NULL, heartbeat_at = NULL, last_error = :erro,
    last_error_context = :contexto,
    next_run_at = CASE WHEN :status = 'RETRY_WAIT'
        THEN now() + (:retry_seconds * interval '1 second')
        ELSE next_run_at END,
    updated_at = now()
WHERE id = :job_id AND source = 'TCE-CE' AND status = 'RUNNING'
  AND lease_token = :lease_token
""").bindparams(bindparam("contexto", type_=JSONB))


def falhar_job_session(
    session: Session,
    *,
    job: JobTceReivindicado,
    erro: str,
    retryable: bool,
    retry_after: timedelta | None = None,
    contexto: dict[str, object] | None = None,
) -> str | None:
    repetir = retryable and job.attempts < job.max_attempts
    if repetir:
        limite = min(3600.0, 2.0 * (2 ** max(0, job.attempts - 1)))
        atraso = retry_after or timedelta(seconds=random.uniform(0, limite))
        status = RETRY_WAIT
    else:
        atraso = timedelta(0)
        status = FAILED
    resultado = session.execute(
        _SQL_FALHAR,
        {
            "status": status,
            "erro": erro[:2048],
            "contexto": contexto or {},
            "retry_seconds": atraso.total_seconds(),
            "job_id": job.id,
            "lease_token": job.lease_token,
        },
    )
    if resultado.rowcount != 1:
        return None
    _evento(
        session,
        job_id=job.id,
        event_type=status,
        attempt=job.attempts,
        details=contexto,
    )
    return status


def adiar_job_session(
    session: Session,
    *,
    job: JobTceReivindicado,
    motivo: str,
    aguardar: timedelta,
) -> bool:
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs
        SET status = 'RETRY_WAIT', attempts = GREATEST(0, attempts - 1),
            locked_by = NULL, lease_token = NULL, lease_until = NULL,
            heartbeat_at = NULL, last_error = :motivo,
            next_run_at = now() + (:retry_seconds * interval '1 second'),
            updated_at = now()
        WHERE id = :job_id AND source = 'TCE-CE' AND status = 'RUNNING'
          AND lease_token = :lease_token
        """),
        {
            "motivo": motivo[:2048],
            "retry_seconds": max(1.0, aguardar.total_seconds()),
            "job_id": job.id,
            "lease_token": job.lease_token,
        },
    )
    if resultado.rowcount == 1:
        _evento(
            session,
            job_id=job.id,
            event_type="DEFERRED",
            attempt=job.attempts,
            details={"motivo": motivo[:500]},
        )
        return True
    return False


def recuperar_leases_expirados_session(session: Session) -> int:
    registros = session.execute(
        text("""
        UPDATE ingestion_jobs
        SET status = CASE WHEN attempts >= max_attempts THEN 'FAILED' ELSE 'PENDING' END,
            locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL,
            last_error = CASE WHEN attempts >= max_attempts
                THEN 'lease TCE expirado; limite de tentativas atingido'
                ELSE 'lease TCE expirado; job devolvido a fila' END,
            last_error_context = jsonb_build_object(
                'categoria', 'lease_expirado', 'attempts', attempts,
                'max_attempts', max_attempts,
                'acao', CASE WHEN attempts >= max_attempts THEN 'falha_final' ELSE 'reagendado' END
            ),
            next_run_at = now(), updated_at = now()
        WHERE source = 'TCE-CE' AND status = 'RUNNING' AND lease_until <= now()
        RETURNING id, attempts, max_attempts, status
        """)
    ).mappings().all()
    for registro in registros:
        status = str(registro["status"])
        attempts = int(registro["attempts"])
        max_attempts = int(registro["max_attempts"])
        _evento(
            session,
            job_id=int(registro["id"]),
            event_type="LEASE_EXPIRED_FAILED" if status == FAILED else "LEASE_EXPIRED",
            attempt=attempts,
            details={
                "categoria": "lease_expirado",
                "status_resultante": status,
                "attempts": attempts,
                "max_attempts": max_attempts,
            },
        )
    return len(registros)


def recuperar_leases_expirados() -> int:
    with orm.main_session() as session:
        return recuperar_leases_expirados_session(session)


def replay_job_session(session: Session, *, job_id: int) -> bool:
    """Reativa explicitamente um job TCE encerrado em FAILED."""
    if job_id < 1:
        raise ValueError("job_id deve ser positivo")
    resultado = session.execute(
        text("""
        WITH candidato AS (
            SELECT id
            FROM ingestion_jobs
            WHERE id = :job_id AND source = 'TCE-CE' AND status = 'FAILED'
            FOR UPDATE SKIP LOCKED
        )
        UPDATE ingestion_jobs AS job
        SET status = 'PENDING', attempts = 0, locked_by = NULL,
            lease_token = NULL, lease_until = NULL, heartbeat_at = NULL,
            next_run_at = now(), replay_count = replay_count + 1,
            last_replayed_at = now(), updated_at = now()
        FROM candidato
        WHERE job.id = candidato.id
          AND NOT EXISTS (
              SELECT 1 FROM ingestion_jobs ativo
              WHERE ativo.dedupe_key = job.dedupe_key AND ativo.id <> job.id
                AND ativo.status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')
          )
        RETURNING job.id
        """),
        {"job_id": job_id},
    ).scalar_one_or_none()
    if resultado is None:
        return False
    _evento(
        session,
        job_id=int(resultado),
        event_type="REPLAYED",
        details={"selector_job_id": job_id},
    )
    return True


def replay_job(*, job_id: int) -> bool:
    with orm.main_session() as session:
        return replay_job_session(session, job_id=job_id)


class WorkerTce:
    """Reserva um job por vez e executa o handler fora da transacao curta."""

    def __init__(
        self,
        worker_id: str,
        handler: Callable[[JobTceReivindicado], None],
        *,
        lease_seconds: int,
    ) -> None:
        self.worker_id = worker_id
        self.handler = handler
        self.lease_seconds = lease_seconds

    def _manter_heartbeat(
        self,
        job: JobTceReivindicado,
        parar: Event,
        lease_perdido: Event,
    ) -> None:
        intervalo = max(1.0, min(30.0, self.lease_seconds / 3))
        while not parar.wait(intervalo):
            try:
                renovado = renovar_lease(
                    job_id=job.id,
                    lease_token=job.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                logger.exception(
                    "Falha ao renovar lease do job TCE",
                    extra={"job_id": job.id, "job_type": job.job_type},
                )
                continue
            if not renovado:
                lease_perdido.set()
                logger.warning(
                    "Heartbeat detectou lease TCE perdido",
                    extra={"job_id": job.id, "job_type": job.job_type},
                )
                return

    def executar_uma_vez(self) -> bool:
        job = reivindicar_proximo_job(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        parar_heartbeat = Event()
        lease_perdido = Event()
        heartbeat = Thread(
            target=self._manter_heartbeat,
            args=(job, parar_heartbeat, lease_perdido),
            name=f"tce-heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self.handler(job)
        except Exception as error:
            retryable = bool(getattr(error, "retryable", True))
            retry_after = getattr(error, "retry_after", None)
            with orm.main_session() as session:
                if getattr(error, "reagendar_sem_tentativa", False):
                    status = (
                        RETRY_WAIT
                        if adiar_job_session(
                            session,
                            job=job,
                            motivo=f"{type(error).__name__}: {error}",
                            aguardar=retry_after or timedelta(minutes=15),
                        )
                        else None
                    )
                else:
                    status = falhar_job_session(
                        session,
                        job=job,
                        erro=f"{type(error).__name__}: {error}",
                        retryable=retryable,
                        retry_after=retry_after,
                        contexto={
                            **(getattr(error, "contexto", None) or {}),
                            "categoria": (getattr(error, "contexto", None) or {}).get(
                                "categoria", type(error).__name__
                            ),
                            "retryable": retryable,
                            "scope_key": job.scope_key,
                        },
                    )
            logger.warning(
                "Job TCE falhou",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "status": status,
                    "error_type": type(error).__name__,
                },
            )
        else:
            concluido = False
            if not lease_perdido.is_set():
                with orm.main_session() as session:
                    concluido = concluir_job_session(
                        session,
                        job_id=job.id,
                        lease_token=job.lease_token,
                    )
            if not concluido:
                logger.warning(
                    "Job TCE perdeu o lease antes da conclusao",
                    extra={"job_id": job.id, "job_type": job.job_type},
                )
        finally:
            parar_heartbeat.set()
            heartbeat.join(timeout=2)
        return True


__all__ = [
    "FAILED",
    "JobTceReivindicado",
    "NovoJobTce",
    "PENDING",
    "RETRY_WAIT",
    "SOURCE",
    "SUCCEEDED",
    "WorkerTce",
    "adiar_job_session",
    "agendar_job_unico",
    "agendar_job_unico_session",
    "agendar_lote_session",
    "concluir_job_session",
    "falhar_job_session",
    "recuperar_leases_expirados",
    "recuperar_leases_expirados_session",
    "replay_job",
    "replay_job_session",
    "reivindicar_proximo_job_session",
    "renovar_lease",
    "renovar_lease_session",
]
