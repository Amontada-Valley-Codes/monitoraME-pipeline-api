"""Fila persistente PNCP baseada exclusivamente em PostgreSQL."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
import time
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, func, select, text
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
PNCP_QUERY_CONFIG_VERSION = "v1"


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
    parameters: dict[str, Any] = field(default_factory=dict)
    config_version: str | None = None


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
    parameters: dict[str, Any] = field(default_factory=dict)
    config_version: str | None = None


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
    if not isinstance(job.parameters, dict):
        raise ValueError("parameters deve ser um objeto JSON")
    try:
        json.dumps(job.parameters, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("parameters deve conter somente valores JSON validos") from exc
    if job.config_version is not None and not job.config_version.strip():
        raise ValueError("config_version nao pode ser vazia")


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
            parameters=job.parameters, config_version=job.config_version,
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
      AND attempts < max_attempts
      AND (:somente_backfill = false OR job_type = 'backfill')
      AND (CAST(:job_type AS text) IS NULL OR job_type = CAST(:job_type AS text))
    ORDER BY
      CASE WHEN job_type = 'normalize' AND status = 'RETRY_WAIT' THEN 1 ELSE 0 END ASC,
      priority DESC, next_run_at ASC, created_at ASC
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
    job_type: str | None = None,
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
            "job_type": job_type,
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
        parameters=registro.get("parameters") or {}, config_version=registro.get("config_version"),
    )
    _registrar_evento_session(session, job_id=job.id, event_type="STARTED", attempt=job.attempts)
    return job


def reivindicar_proximo_job(**kwargs: Any) -> JobReivindicado | None:
    with orm.main_session() as session:
        return reivindicar_proximo_job_session(session, **kwargs)


def renovar_lease_session(session: Session, *, job_id: int, lease_token: str, lease_seconds: int = 120) -> bool:
    if lease_seconds < 1:
        raise ValueError("lease_seconds deve ser positivo")
    resultado = session.execute(
        text("""
        UPDATE ingestion_jobs SET lease_until = now() + (:lease_seconds * interval '1 second'),
            heartbeat_at = now(), updated_at = now()
        WHERE id = :job_id AND status = 'RUNNING' AND lease_token = :lease_token
          AND lease_until > now()
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
          AND lease_until > now()
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
    contexto_retry = {
        **(contexto or {}),
        "retry_reason": decisao.motivo,
        "retry_seconds": decisao.atraso.total_seconds() if decisao.atraso else 0,
    }
    parametros = {
        "job_id": job.id, "lease_token": job.lease_token, "status": status,
        "erro": erro[:2048],
        "contexto": contexto_retry,
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
          AND lease_until > now()
        """).bindparams(bindparam("contexto", type_=JSONB))
    resultado = session.execute(statement, parametros)
    if resultado.rowcount == 1:
        _registrar_evento_session(
            session, job_id=job.id, event_type=status, attempt=job.attempts,
            details=contexto_retry,
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
          AND lease_until > now()
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
          AND lease_until > now()
        """).bindparams(bindparam("contexto", type_=JSONB)),
        {"job_id": job.id, "lease_token": job.lease_token, "motivo": motivo[:2048],
         "contexto": contexto or {}, "aguardar_seconds": max(0, aguardar.total_seconds())},
    )
    if resultado.rowcount == 1:
        _registrar_evento_session(session, job_id=job.id, event_type="DEFERRED", attempt=job.attempts, details=contexto)
        return True
    return False


def recuperar_leases_expirados_session(
    session: Session,
    *,
    job_type: str | None = None,
    job_types: tuple[str, ...] | None = None,
    source: str | None = None,
) -> int:
    if job_type is not None and job_types is not None:
        raise ValueError("informe job_type ou job_types, nao ambos")
    if job_types is not None and not job_types:
        return 0
    filtro_tipo = " AND job_type = :job_type" if job_type is not None else ""
    parametros_filtro: dict[str, object] = {}
    if job_type is not None:
        parametros_filtro["job_type"] = job_type
    if job_types is not None:
        nomes = []
        for indice, tipo in enumerate(dict.fromkeys(job_types)):
            nome = f"job_type_{indice}"
            nomes.append(f":{nome}")
            parametros_filtro[nome] = tipo
        filtro_tipo = f" AND job_type IN ({', '.join(nomes)})"
    filtro_fonte = " AND source = :source" if source is not None else ""
    if source is not None:
        parametros_filtro["source"] = source
    exhausted = session.execute(text(f"""
        UPDATE ingestion_jobs
        SET status = 'FAILED', locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL,
            last_error = coalesce(last_error, 'tentativas esgotadas'), updated_at = now()
        WHERE status IN ('PENDING', 'RETRY_WAIT') AND attempts >= max_attempts
          AND next_run_at <= now(){filtro_tipo}{filtro_fonte}
        RETURNING id, attempts
    """), parametros_filtro).mappings().all()
    for registro in exhausted:
        _registrar_evento_session(
            session,
            job_id=int(registro["id"]),
            event_type="FAILED",
            attempt=int(registro["attempts"]),
            details={"motivo": "tentativas esgotadas durante recuperacao"},
        )
    expirados = session.execute(text(f"""
        SELECT id, attempts, max_attempts
        FROM ingestion_jobs
        WHERE status = 'RUNNING' AND lease_until <= now(){filtro_tipo}{filtro_fonte}
        FOR UPDATE SKIP LOCKED
    """), parametros_filtro).mappings().all()
    politica = PoliticaRetryPncp.do_ambiente() if source == "PNCP" else PoliticaRetryPncp()
    recuperados = 0
    for registro in expirados:
        tentativas = int(registro["attempts"])
        limite = int(registro.get("max_attempts", politica.max_attempts))
        decisao = decidir_retry(
            attempts=tentativas,
            max_attempts=limite,
            retryable=True,
            politica=politica,
        )
        novo_status = PENDING if decisao.repetir else FAILED
        atraso = decisao.atraso if source == "PNCP" else timedelta()
        motivo_retry = (
            decisao.motivo
            if source == "PNCP"
            else "lease expirado; reagendado sem atraso"
        )
        resultado = session.execute(text(f"""
            UPDATE ingestion_jobs
            SET status = '{novo_status}',
                locked_by = NULL, lease_token = NULL, lease_until = NULL,
                heartbeat_at = NULL,
                last_error = 'lease expirado; job devolvido a fila',
                last_error_context = CAST(:contexto AS jsonb),
                next_run_at = now() + (:retry_seconds * interval '1 second'),
                updated_at = now()
            WHERE id = :job_id AND status = 'RUNNING' AND lease_until <= now()
            RETURNING id
        """).bindparams(bindparam("contexto", type_=JSONB)), {
            "job_id": int(registro["id"]),
            "retry_seconds": atraso.total_seconds() if atraso else 0,
            "contexto": {
                "categoria": "lease_expirado",
                "retry_reason": motivo_retry,
                "retry_seconds": atraso.total_seconds() if atraso else None,
            },
        }).mappings().first()
        if resultado is None:
            continue
        recuperados += 1
        _registrar_evento_session(
            session,
            job_id=int(registro["id"]),
            event_type="LEASE_EXPIRED",
            attempt=tentativas,
            details={
                "motivo": "worker sem heartbeat",
                "status": novo_status,
                "retry_reason": motivo_retry,
                "retry_seconds": atraso.total_seconds() if atraso else None,
            },
        )
    return recuperados


def recuperar_leases_expirados(
    *,
    source: str | None = "PNCP",
    job_type: str | None = None,
    job_types: tuple[str, ...] | None = None,
) -> int:
    with orm.main_session() as session:
        return recuperar_leases_expirados_session(
            session, source=source, job_type=job_type, job_types=job_types
        )


def replay_jobs_session(
    session: Session,
    *,
    source: str = "PNCP",
    job_id: int | None = None,
    flow: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[int]:
    """Reativa falhas conscientemente, sem concorrer com dedupe_key ativo.

    A seleção usa sobreposição de janela e aceita somente ``FAILED`` e
    ``QUARANTINED``. Jobs bem-sucedidos jamais são alterados por este caminho.
    """
    if source != "PNCP":
        raise ValueError("replay PNCP aceita somente source='PNCP'")
    if flow is not None and (not flow.strip() or ":" in flow or "%" in flow or "_" in flow):
        raise ValueError("flow deve ser um prefixo exato de scope_key")
    if job_id is None and flow is None and window_start is None and window_end is None:
        raise ValueError("informe job_id, flow ou janela para replay")
    if (window_start is None) != (window_end is None):
        raise ValueError("window_start e window_end devem ser informados juntos")
    if window_start and (window_start.tzinfo is None or window_end is None or window_end.tzinfo is None or window_end < window_start):
        raise ValueError("janela de replay deve ter timezone e ordem valida")
    filtros = ["source = :source", "status IN ('FAILED', 'QUARANTINED')"]
    parametros: dict[str, object] = {"source": source}
    if job_id is not None:
        filtros.append("id = :job_id")
        parametros["job_id"] = job_id
    if flow is not None:
        filtros.append("left(scope_key, length(:flow)) = :flow AND substr(scope_key, length(:flow) + 1, 1) = ':'")
        parametros["flow"] = flow
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
                "selector_job_id": job_id, "source": source, "flow": flow,
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


def _contexto_log_job(job: JobReivindicado, *, worker_id: str) -> dict[str, Any]:
    flow = job.parameters.get("flow")
    if not isinstance(flow, str):
        flow = "publicacao" if job.scope_key.startswith("backfill:") else job.scope_key.split(":", 1)[0]
    return {
        "worker_id": worker_id,
        "job_id": job.id,
        "job_type": job.job_type,
        "entity_type": job.entity_type,
        "source": job.source,
        "flow": flow,
        "uf": job.parameters.get("uf"),
        "modalidade": job.parameters.get("codigoModalidadeContratacao"),
        "pagina_inicial": job.page,
        "page_size": job.parameters.get("tamanhoPagina"),
        "window_start": job.window_start,
        "window_end": job.window_end,
        "raw_record_id": job.raw_record_id,
        "attempt": job.attempts,
        "max_attempts": job.max_attempts,
        "attempts_remaining": max(0, job.max_attempts - job.attempts),
    }


class WorkerPncp:
    """Executa um job por vez; o handler roda fora da transacao de reserva."""

    def __init__(self, worker_id: str, handler: Callable[[JobReivindicado], None], *, lease_seconds: int = 120, backfill_a_cada: int = 10, job_type: str | None = None) -> None:
        self.worker_id = worker_id
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.backfill_a_cada = max(1, backfill_a_cada)
        self.job_type = job_type
        self._reservas = 0

    def executar_uma_vez(self) -> bool:
        self._reservas += 1
        somente_backfill = self._reservas % self.backfill_a_cada == 0
        job = reivindicar_proximo_job(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
            somente_backfill=somente_backfill, job_type=self.job_type,
        )
        if job is None:
            return False
        inicio_execucao = time.monotonic()
        contexto_job = _contexto_log_job(job, worker_id=self.worker_id)
        registrar_progresso_coleta = job.job_type in {"incremental", "backfill"}
        if registrar_progresso_coleta:
            logger.info("Job PNCP iniciado", extra=contexto_job)
        try:
            self._executar_com_heartbeat(job)
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
                if status is not None:
                    contexto_persistido = session.scalar(
                        select(IngestionJob.last_error_context).where(IngestionJob.id == job.id)
                    )
                    if isinstance(contexto_persistido, dict):
                        contexto = contexto_persistido
            detalhes_falha: dict[str, Any] = {
                **contexto_job,
                "status": status,
                "error": str(exc)[:2048],
                "error_type": type(exc).__name__,
                "retryable": retryable,
                "error_context": contexto,
                "duration_seconds": round(time.monotonic() - inicio_execucao, 3),
            }
            if retry_after is not None:
                detalhes_falha["retry_after_seconds"] = retry_after.total_seconds()
            if "retry_seconds" in contexto:
                detalhes_falha["retry_seconds"] = contexto["retry_seconds"]
            if job.job_type == "enrich":
                detalhes_falha.update(_detalhes_job_enriquecimento(job))
                detalhes_falha.update(_contar_fila_enriquecimento())
            logger.warning("Job PNCP falhou", extra=detalhes_falha)
        else:
            with orm.main_session() as session:
                concluido = concluir_job_session(session, job_id=job.id, lease_token=job.lease_token)
            if not concluido:
                logger.warning(
                    "Job PNCP perdeu lease antes da conclusao",
                    extra={
                        **contexto_job,
                        "duration_seconds": round(time.monotonic() - inicio_execucao, 3),
                    },
                )
            elif job.job_type == "enrich":
                detalhes = _detalhes_job_enriquecimento(job)
                detalhes.update(_contar_fila_enriquecimento())
                logger.info("Registro de enriquecimento PNCP concluido", extra={"job_id": job.id, **detalhes})
            if concluido and registrar_progresso_coleta:
                logger.info(
                    "Job PNCP concluido",
                    extra={
                        **contexto_job,
                        "status": SUCCEEDED,
                        "duration_seconds": round(time.monotonic() - inicio_execucao, 3),
                    },
                )
        return True

    def _executar_com_heartbeat(self, job: JobReivindicado) -> None:
        parar_heartbeat = Event()
        intervalo = max(0.05, min(60.0, self.lease_seconds / 3))

        def manter_lease() -> None:
            while not parar_heartbeat.wait(intervalo):
                try:
                    renovado = renovar_lease(
                        job_id=job.id,
                        lease_token=job.lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:
                    logger.exception(
                        "Falha ao renovar lease de job PNCP",
                        extra={"job_id": job.id, "worker_id": self.worker_id},
                    )
                    continue
                if not renovado:
                    logger.warning(
                        "Worker perdeu lease de job PNCP",
                        extra={"job_id": job.id, "worker_id": self.worker_id},
                    )
                    return

        heartbeat = Thread(
            target=manter_lease,
            name=f"pncp-heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            self.handler(job)  # I/O e processamento ficam fora da transacao de reserva.
        finally:
            parar_heartbeat.set()
            heartbeat.join()

    def executar_loop(self, *, parar: Callable[[], bool], idle_seconds: float = 1.0) -> None:
        while not parar():
            if not self.executar_uma_vez():
                time.sleep(idle_seconds)


def _detalhes_job_enriquecimento(job: JobReivindicado) -> dict[str, str | None]:
    partes = job.scope_key.split(":", 3)
    if len(partes) == 4 and partes[0] == "enrich":
        return {"recurso": partes[1], "external_id": partes[2]}
    return {"recurso": None, "external_id": None}


def _contar_fila_enriquecimento() -> dict[str, int]:
    with orm.main_session() as session:
        total = session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.job_type == "enrich", IngestionJob.status.in_(ACTIVE_STATUSES))
        ) or 0
        documentos = session.scalar(
            select(func.count())
            .select_from(IngestionJob)
            .where(
                IngestionJob.job_type == "enrich",
                IngestionJob.scope_key.like("enrich:documents:%"),
                IngestionJob.status.in_(ACTIVE_STATUSES),
            )
        ) or 0
    return {"fila_enriquecimento_restante": int(total), "documentos_na_fila": int(documentos)}


__all__ = [
    "ACTIVE_STATUSES", "AgendadorPncp", "FAILED", "NovoJob", "PENDING", "RETRY_WAIT",
    "RUNNING", "SUCCEEDED", "WorkerPncp", "agendar_job", "agendar_job_session",
    "adiar_job_sem_tentativa_session", "concluir_job_session", "falhar_job_session",
    "quarentenar_job_session", "replay_jobs", "replay_jobs_session",
    "recuperar_leases_expirados", "recuperar_leases_expirados_session",
    "renovar_lease", "renovar_lease_session", "reivindicar_proximo_job",
    "reivindicar_proximo_job_session",
]
