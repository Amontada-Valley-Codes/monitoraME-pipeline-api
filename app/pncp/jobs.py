"""Fila persistente PNCP baseada exclusivamente em PostgreSQL."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import IngestionJob, IngestionJobEvent
from app.pncp.retry import PoliticaRetryPncp, decidir_retry


logger = logging.getLogger(__name__)

PENDING = "PENDING"
RUNNING = "RUNNING"
RETRY_WAIT = "RETRY_WAIT"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
QUARANTINED = "QUARANTINED"
ACTIVE_STATUSES = (PENDING, RUNNING, RETRY_WAIT)
BACKFILL = "backfill"


@dataclass(frozen=True)
class NovoJob:
    job_type: str
    entity_type: str
    source: str
    scope_key: str
    dedupe_key: str
    priority: int
    next_run_at: datetime
    max_attempts: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    page: int | None = None
    raw_record_id: int | None = None


@dataclass(frozen=True)
class JobReivindicado:
    id: int
    job_type: str
    entity_type: str
    source: str
    scope_key: str
    dedupe_key: str
    attempts: int
    max_attempts: int
    lease_token: str
    raw_record_id: int | None
    window_start: datetime | None
    window_end: datetime | None
    page: int | None


def _registrar_evento_session(
    session: Session, *, job_id: int, event_type: str, attempt: int | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(IngestionJobEvent(
        job_id=job_id, event_type=event_type, attempt=attempt, details=details or {},
    ))


def _validar_novo_job(job: NovoJob) -> None:
    if not all((job.job_type.strip(), job.entity_type.strip(), job.source.strip(), job.scope_key.strip(), job.dedupe_key.strip())):
        raise ValueError("job_type, entity_type, source, scope_key e dedupe_key sao obrigatorios")
    if job.next_run_at.tzinfo is None:
        raise ValueError("next_run_at deve incluir timezone")
    if job.max_attempts is not None and job.max_attempts < 1:
        raise ValueError("max_attempts deve ser maior que zero")


def agendar_job_session(session: Session, job: NovoJob) -> tuple[int, bool]:
    """Agenda um job ou devolve o equivalente que ainda esta ativo."""
    _validar_novo_job(job)
    max_attempts = job.max_attempts or PoliticaRetryPncp.do_ambiente().max_attempts
    stmt = (
        insert(IngestionJob)
        .values(
            job_type=job.job_type, entity_type=job.entity_type, source=job.source,
            scope_key=job.scope_key, dedupe_key=job.dedupe_key, priority=job.priority,
            status=PENDING, next_run_at=job.next_run_at, max_attempts=max_attempts,
            window_start=job.window_start, window_end=job.window_end, page=job.page,
            raw_record_id=job.raw_record_id,
        )
        .on_conflict_do_nothing(
            index_elements=[IngestionJob.dedupe_key],
            index_where=text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
        )
        .returning(IngestionJob.id)
    )
    job_id = session.scalar(stmt)
    if job_id is not None:
        return int(job_id), True
    job_id = session.scalar(
        select(IngestionJob.id).where(
            IngestionJob.dedupe_key == job.dedupe_key,
            IngestionJob.status.in_(ACTIVE_STATUSES),
        )
    )
    if job_id is None:  # pragma: no cover - concorrencia externa inesperada
        raise RuntimeError("job ativo nao encontrado apos conflito de dedupe")
    return int(job_id), False


def agendar_job(job: NovoJob) -> tuple[int, bool]:
    with orm.main_session() as session:
        return agendar_job_session(session, job)


_SQL_REIVINDICAR = text("""
WITH candidate AS (
    SELECT id
    FROM ingestion_jobs
    WHERE status IN ('PENDING', 'RETRY_WAIT')
      AND next_run_at <= now()
      AND (:somente_backfill = false OR job_type = 'backfill')
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
    lease_seconds: int = 120,
    somente_backfill: bool = False,
) -> JobReivindicado | None:
    """Reserva em uma unica transacao curta; nao executa o trabalho."""
    if not worker_id.strip() or lease_seconds < 1:
        raise ValueError("worker_id e lease_seconds valido sao obrigatorios")
    registro = session.execute(
        _SQL_REIVINDICAR,
        {
            "worker_id": worker_id,
            "lease_token": str(uuid4()),
            "lease_seconds": lease_seconds,
            "somente_backfill": somente_backfill,
        },
    ).mappings().first()
    if registro is None:
        return None
    job = JobReivindicado(
        id=int(registro["id"]), job_type=registro["job_type"],
        entity_type=registro["entity_type"], source=registro["source"],
        scope_key=registro["scope_key"], dedupe_key=registro["dedupe_key"],
        attempts=int(registro["attempts"]), max_attempts=int(registro["max_attempts"]),
        lease_token=registro["lease_token"], raw_record_id=registro["raw_record_id"],
        window_start=registro["window_start"], window_end=registro["window_end"], page=registro["page"],
    )
    _registrar_evento_session(session, job_id=job.id, event_type="STARTED", attempt=job.attempts)
    return job


def reivindicar_proximo_job(**kwargs: Any) -> JobReivindicado | None:
    with orm.main_session() as session:
        return reivindicar_proximo_job_session(session, **kwargs)


def renovar_lease_session(session: Session, *, job_id: int, lease_token: str, lease_seconds: int = 120) -> bool:
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs SET lease_until = now() + (:lease_seconds * interval '1 second'),
            heartbeat_at = now(), updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
        """),
        {"job_id": job_id, "lease_token": lease_token, "lease_seconds": lease_seconds},
    )
    return resultado.rowcount == 1


def renovar_lease(*, job_id: int, lease_token: str, lease_seconds: int = 120) -> bool:
    """Heartbeat publico para handlers de detalhe, documentos ou paginas longas."""
    with orm.main_session() as session:
        return renovar_lease_session(
            session, job_id=job_id, lease_token=lease_token, lease_seconds=lease_seconds
        )


def concluir_job_session(session: Session, *, job_id: int, lease_token: str) -> bool:
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs SET status = 'SUCCEEDED', locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, last_error = NULL,
            success_count = success_count + 1, updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
        """), {"job_id": job_id, "lease_token": lease_token},
    )
    concluido = resultado.rowcount == 1
    if concluido:
        _registrar_evento_session(session, job_id=job_id, event_type="SUCCEEDED")
    return concluido


def falhar_job_session(
    session: Session,
    *,
    job: JobReivindicado,
    erro: str,
    retryable: bool = True,
    retry_after: timedelta | None = None,
    contexto: dict[str, object] | None = None,
    politica: PoliticaRetryPncp | None = None,
) -> str | None:
    """Libera o lease e programa retry, ou encerra o job ao esgotar tentativas."""
    decisao = decidir_retry(
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        retryable=retryable,
        retry_after=retry_after,
        politica=politica,
    )
    status = RETRY_WAIT if decisao.repetir else FAILED
    parametros = {
        "job_id": job.id, "lease_token": job.lease_token, "status": status,
        "erro": erro[:2048],
        "contexto": {**(contexto or {}), "retry_reason": decisao.motivo},
        "retry_seconds": decisao.atraso.total_seconds() if decisao.atraso else 0,
    }
    statement = text("""
        UPDATE ingestion_jobs SET status = :status, locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, last_error = :erro,
            last_error_context = CAST(:contexto AS jsonb),
            next_run_at = CASE WHEN :status = 'RETRY_WAIT'
                THEN now() + (:retry_seconds * interval '1 second') ELSE next_run_at END,
            updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
        """).bindparams(bindparam("contexto", type_=JSONB))
    resultado = session.execute(statement, parametros)
    if resultado.rowcount == 1:
        _registrar_evento_session(
            session, job_id=job.id, event_type=status, attempt=job.attempts,
            details={**(contexto or {}), "retry_reason": decisao.motivo},
        )
        return status
    return None


def quarentenar_job_session(
    session: Session, *, job: JobReivindicado, erro: str, contexto: dict[str, object] | None = None
) -> bool:
    """Encerra uma inconsistência estrutural sem apagar RAW ou diagnóstico."""
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs SET status = 'QUARANTINED', locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, last_error = :erro,
            last_error_context = CAST(:contexto AS jsonb), updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
        """).bindparams(bindparam("contexto", type_=JSONB)),
        {"job_id": job.id, "lease_token": job.lease_token, "erro": erro[:2048], "contexto": contexto or {}},
    )
    if resultado.rowcount == 1:
        _registrar_evento_session(session, job_id=job.id, event_type=QUARANTINED, attempt=job.attempts, details=contexto)
        return True
    return False


def adiar_job_sem_tentativa_session(
    session: Session, *, job: JobReivindicado, motivo: str, aguardar: timedelta, contexto: dict[str, object] | None = None
) -> bool:
    """Devolve um job não iniciado à fila sem consumir tentativa de HTTP."""
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs SET status = 'RETRY_WAIT', attempts = GREATEST(0, attempts - 1),
            locked_by = NULL, lease_token = NULL, lease_until = NULL, heartbeat_at = NULL,
            last_error = :motivo, last_error_context = CAST(:contexto AS jsonb),
            next_run_at = now() + (:aguardar_seconds * interval '1 second'), updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
        """).bindparams(bindparam("contexto", type_=JSONB)),
        {"job_id": job.id, "lease_token": job.lease_token, "motivo": motivo[:2048],
         "contexto": contexto or {}, "aguardar_seconds": max(0, aguardar.total_seconds())},
    )
    if resultado.rowcount == 1:
        _registrar_evento_session(session, job_id=job.id, event_type="DEFERRED", attempt=job.attempts, details=contexto)
        return True
    return False


def recuperar_leases_expirados_session(session: Session) -> int:
    resultado = session.execute(text("""
        UPDATE ingestion_jobs SET status = 'PENDING', locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL,
            last_error = 'lease expirado; job devolvido a fila', next_run_at = now(), updated_at = now()
        WHERE status = 'RUNNING' AND lease_until <= now()
        RETURNING id, attempts
    """))
    recuperados = resultado.mappings().all()
    for registro in recuperados:
        _registrar_evento_session(
            session, job_id=int(registro["id"]), event_type="LEASE_EXPIRED",
            attempt=int(registro["attempts"]), details={"motivo": "worker sem heartbeat"},
        )
    return len(recuperados)


def recuperar_leases_expirados() -> int:
    with orm.main_session() as session:
        return recuperar_leases_expirados_session(session)


def replay_jobs_session(
    session: Session,
    *,
    job_id: int | None = None,
    flow: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[int]:
    """Reativa falhas conscientemente, sem concorrer com dedupe_key ativo.

    A seleção usa sobreposição de janela e aceita somente ``FAILED`` e
    ``QUARANTINED``. Jobs bem-sucedidos jamais são alterados por este caminho.
    """
    if job_id is None and flow is None and window_start is None and window_end is None:
        raise ValueError("informe job_id, flow ou janela para replay")
    if (window_start is None) != (window_end is None):
        raise ValueError("window_start e window_end devem ser informados juntos")
    if window_start and (window_start.tzinfo is None or window_end is None or window_end.tzinfo is None or window_end < window_start):
        raise ValueError("janela de replay deve ter timezone e ordem valida")
    filtros = ["status IN ('FAILED', 'QUARANTINED')"]
    parametros: dict[str, object] = {}
    if job_id is not None:
        filtros.append("id = :job_id")
        parametros["job_id"] = job_id
    if flow is not None:
        filtros.append("scope_key LIKE :flow")
        parametros["flow"] = f"{flow}:%"
    if window_start is not None and window_end is not None:
        filtros.append("window_end >= :window_start AND window_start <= :window_end")
        parametros.update({"window_start": window_start, "window_end": window_end})
    where = " AND ".join(filtros)
    statement = text(f"""
        WITH candidatos AS (
            SELECT id
            FROM ingestion_jobs
            WHERE {where}
            FOR UPDATE SKIP LOCKED
        )
        UPDATE ingestion_jobs AS job
        SET status = 'PENDING', attempts = 0, locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, next_run_at = now(),
            replay_count = replay_count + 1, last_replayed_at = now(), updated_at = now()
        FROM candidatos
        WHERE job.id = candidatos.id
          AND NOT EXISTS (
              SELECT 1 FROM ingestion_jobs ativo
              WHERE ativo.dedupe_key = job.dedupe_key AND ativo.id <> job.id
                AND ativo.status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')
          )
        RETURNING job.id
    """)
    registros = session.execute(statement, parametros).mappings().all()
    ids = [int(registro["id"]) for registro in registros]
    for replay_id in ids:
        _registrar_evento_session(
            session, job_id=replay_id, event_type="REPLAYED", details={
                "selector_job_id": job_id, "flow": flow,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
            },
        )
    return ids


def replay_jobs(**kwargs: Any) -> list[int]:
    with orm.main_session() as session:
        return replay_jobs_session(session, **kwargs)


class AgendadorPncp:
    """Adapta uma politica de janelas para a fila, sem conhecer HTTP ou SQL."""

    def __init__(self, produtor: Callable[[], list[NovoJob]]) -> None:
        self.produtor = produtor

    def executar_uma_vez(self) -> list[tuple[int, bool]]:
        return [agendar_job(job) for job in self.produtor()]

    def executar_loop(self, *, parar: Callable[[], bool], interval_seconds: float = 30.0) -> None:
        while not parar():
            resultados = self.executar_uma_vez()
            logger.info("Agendador PNCP executado", extra={"jobs_avaliados": len(resultados)})
            time.sleep(interval_seconds)


class WorkerPncp:
    """Executa um job por vez; o handler roda fora da transacao de reserva."""

    def __init__(self, worker_id: str, handler: Callable[[JobReivindicado], None], *, lease_seconds: int = 120, backfill_a_cada: int = 10) -> None:
        self.worker_id = worker_id
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.backfill_a_cada = max(1, backfill_a_cada)
        self._reservas = 0

    def executar_uma_vez(self) -> bool:
        self._reservas += 1
        somente_backfill = self._reservas % self.backfill_a_cada == 0
        job = reivindicar_proximo_job(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
            somente_backfill=somente_backfill,
        )
        if job is None:
            return False
        try:
            self.handler(job)  # HTTP/processamento fica deliberadamente fora da transacao.
        except Exception as exc:
            retryable = getattr(exc, "retryable", True)
            retry_after = getattr(exc, "retry_after", None)
            contexto = exc.contexto() if hasattr(exc, "contexto") else {"categoria": type(exc).__name__, "retryable": retryable}
            with orm.main_session() as session:
                if getattr(exc, "reagendar_sem_tentativa", False):
                    adiado = adiar_job_sem_tentativa_session(
                        session, job=job, motivo=f"{type(exc).__name__}: {exc}",
                        aguardar=retry_after or timedelta(seconds=1), contexto=contexto,
                    )
                    status = RETRY_WAIT if adiado else None
                elif getattr(exc, "quarantinar", False):
                    quarantinado = quarentenar_job_session(
                        session, job=job, erro=f"{type(exc).__name__}: {exc}", contexto=contexto,
                    )
                    status = QUARANTINED if quarantinado else None
                else:
                    status = falhar_job_session(
                        session, job=job, erro=f"{type(exc).__name__}: {exc}",
                        retryable=retryable, retry_after=retry_after, contexto=contexto,
                    )
            logger.warning("Job PNCP falhou", extra={"job_id": job.id, "status": status, "error": str(exc)[:2048], "retryable": retryable})
        else:
            with orm.main_session() as session:
                concluido = concluir_job_session(session, job_id=job.id, lease_token=job.lease_token)
            if not concluido:
                logger.warning("Job PNCP perdeu lease antes da conclusao", extra={"job_id": job.id})
        return True

    def executar_loop(self, *, parar: Callable[[], bool], idle_seconds: float = 1.0) -> None:
        while not parar():
            if not self.executar_uma_vez():
                time.sleep(idle_seconds)


def executar_recuperador(*, parar: Callable[[], bool], interval_seconds: float = 30.0) -> None:
    while not parar():
        quantidade = recuperar_leases_expirados()
        if quantidade:
            logger.info("Leases PNCP recuperados", extra={"quantidade": quantidade})
        time.sleep(interval_seconds)


__all__ = [
    "ACTIVE_STATUSES", "AgendadorPncp", "BACKFILL", "FAILED", "NovoJob", "PENDING", "RETRY_WAIT",
    "RUNNING", "SUCCEEDED", "WorkerPncp", "agendar_job", "agendar_job_session",
    "adiar_job_sem_tentativa_session", "concluir_job_session", "executar_recuperador", "falhar_job_session",
    "quarentenar_job_session", "replay_jobs", "replay_jobs_session",
    "recuperar_leases_expirados", "recuperar_leases_expirados_session",
    "renovar_lease", "renovar_lease_session", "reivindicar_proximo_job",
    "reivindicar_proximo_job_session",
]
