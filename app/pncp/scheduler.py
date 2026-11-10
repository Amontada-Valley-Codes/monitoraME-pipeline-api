"""Cria janelas recorrentes PNCP na fila PostgreSQL, sem executar coleta."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import IngestionJob, PncpSyncCursor
from app.pncp.atualizacao import agendar_atualizacao
from app.pncp.checkpoints import ChaveCursor, OVERLAP_PADRAO, proxima_janela
from app.pncp.ingestion import (
    MODALIDADES_PRIORITARIAS,
    UFS_NORDESTE,
    agendar_backfill_anual,
    agendar_publicacao,
)
from app.pncp.jobs import NovoJob, agendar_job_session


_ADVISORY_LOCK_ID = 0x504E435053434844
_FLOWS_VALIDOS = frozenset(("publicacao", "atualizacao"))
_CICLO_FLOW = "scheduler"
_CICLO_SCOPE = "completo"
_CICLO_INTERVALO_PADRAO = 4 * 60 * 60


@dataclass(frozen=True)
class ConfiguracaoAgendamentoPncp:
    flows: tuple[str, ...] = ("publicacao", "atualizacao")
    modalidades: tuple[int, ...] = MODALIDADES_PRIORITARIAS
    ufs: tuple[str, ...] | None = UFS_NORDESTE
    window_days: int = 1
    page_size: int = 50
    publication_priority: int = 70
    update_priority: int = 80
    backfill_enabled: bool = True
    backfill_start_year: int = 2021
    backfill_window_days: int = 7
    backfill_priority: int = 20
    cycle_interval_seconds: int = _CICLO_INTERVALO_PADRAO

    @classmethod
    def do_ambiente(cls) -> ConfiguracaoAgendamentoPncp:
        flows = _lista_texto("PNCP_SCHEDULER_FLOWS", "publicacao,atualizacao")
        invalidos = set(flows) - _FLOWS_VALIDOS
        if invalidos:
            raise ValueError(f"fluxos PNCP nao suportados: {', '.join(sorted(invalidos))}")
        modalidades = _lista_inteiros(
            "PNCP_SCHEDULER_MODALIDADES",
            ",".join(str(codigo) for codigo in MODALIDADES_PRIORITARIAS),
        )
        ufs_configuradas = os.getenv("PNCP_SCHEDULER_UFS", ",".join(UFS_NORDESTE)).strip()
        ufs = None if ufs_configuradas == "*" else tuple(
            uf.upper() for uf in _lista_texto("PNCP_SCHEDULER_UFS", ",".join(UFS_NORDESTE))
        )
        if ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)):
            raise ValueError("PNCP_SCHEDULER_UFS deve conter UFs suportadas ou *")
        config = cls(
            flows=flows,
            modalidades=modalidades,
            ufs=ufs,
            window_days=_inteiro_positivo("PNCP_SCHEDULER_WINDOW_DAYS", 1),
            page_size=_inteiro_positivo("PNCP_SCHEDULER_PAGE_SIZE", 50),
            publication_priority=int(os.getenv("PNCP_SCHEDULER_PUBLICATION_PRIORITY", "70")),
            update_priority=int(os.getenv("PNCP_SCHEDULER_UPDATE_PRIORITY", "80")),
            backfill_enabled=os.getenv("PNCP_SCHEDULER_BACKFILL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
            backfill_start_year=_inteiro_positivo("PNCP_SCHEDULER_BACKFILL_START_YEAR", 2021),
            backfill_window_days=_inteiro_positivo("PNCP_SCHEDULER_BACKFILL_WINDOW_DAYS", 7),
            backfill_priority=int(os.getenv("PNCP_SCHEDULER_BACKFILL_PRIORITY", "20")),
            cycle_interval_seconds=_inteiro_positivo("PNCP_SCHEDULER_CYCLE_INTERVAL_SECONDS", _CICLO_INTERVALO_PADRAO),
        )
        config.validar()
        return config

    def validar(self) -> None:
        if not self.flows or set(self.flows) - _FLOWS_VALIDOS:
            raise ValueError("flows deve incluir publicacao e/ou atualizacao")
        if not self.modalidades or any(codigo < 1 for codigo in self.modalidades):
            raise ValueError("modalidades deve conter codigos positivos")
        if self.ufs is not None and (not self.ufs or any(uf not in UFS_NORDESTE for uf in self.ufs)):
            raise ValueError("ufs deve conter UFs suportadas ou ser None")
        if self.window_days < 1:
            raise ValueError("window_days deve ser positivo")
        if not 1 <= self.page_size <= 50:
            raise ValueError("page_size deve estar entre 1 e 50")
        if self.backfill_start_year < 2021 or self.backfill_start_year > datetime.now(timezone.utc).year:
            raise ValueError("backfill_start_year deve estar entre 2021 e o ano atual")
        if self.backfill_window_days < 1:
            raise ValueError("backfill_window_days deve ser positivo")
        if self.cycle_interval_seconds < 1:
            raise ValueError("cycle_interval_seconds deve ser positivo")


def _lista_texto(nome: str, padrao: str) -> tuple[str, ...]:
    bruto = os.getenv(nome, padrao)
    return tuple(dict.fromkeys(parte.strip().lower() for parte in bruto.split(",") if parte.strip()))


def _lista_inteiros(nome: str, padrao: str) -> tuple[int, ...]:
    valores = _lista_texto(nome, padrao)
    try:
        return tuple(dict.fromkeys(int(valor) for valor in valores))
    except ValueError as exc:
        raise ValueError(f"{nome} deve conter inteiros separados por virgula") from exc


def _inteiro_positivo(nome: str, padrao: int) -> int:
    try:
        valor = int(os.getenv(nome, str(padrao)))
    except ValueError as exc:
        raise ValueError(f"{nome} deve ser um inteiro positivo") from exc
    if valor < 1:
        raise ValueError(f"{nome} deve ser um inteiro positivo")
    return valor


def _scope_cursor(uf: str | None, modalidade: int) -> str:
    return f"uf:{uf}:modalidade:{modalidade}" if uf else f"modalidade:{modalidade}"


def _planejar_job(
    session: Session,
    *,
    flow: str,
    modalidade: int,
    uf: str | None,
    agora: datetime,
    config: ConfiguracaoAgendamentoPncp,
) -> NovoJob:
    scope_cursor = _scope_cursor(uf, modalidade)
    chave = ChaveCursor("PNCP", "CONTRATACAO", flow, scope_cursor)
    cursor = session.get(
        PncpSyncCursor,
        {
            "source": chave.source,
            "entity_type": chave.entity_type,
            "flow": chave.flow,
            "scope_key": chave.scope_key,
        },
    )
    inicio, fim = proxima_janela(
        chave,
        now=agora,
        size=timedelta(days=config.window_days),
        last_confirmed_end=cursor.last_confirmed_end if cursor else None,
        overlap=OVERLAP_PADRAO,
    )
    if inicio > agora:
        raise ValueError(f"cursor futuro impede planejar janela: {flow}/{scope_cursor}")
    fim = min(fim, inicio + timedelta(days=config.window_days))
    ufs = (uf,) if uf is not None else None
    if flow == "publicacao":
        return agendar_publicacao(
            inicio=inicio,
            fim=fim,
            modalidades=[modalidade],
            ufs=ufs,
            tamanho_pagina=config.page_size,
            prioridade=config.publication_priority,
        )[0]
    return agendar_atualizacao(
        inicio=inicio,
        fim=fim,
        modalidades=[modalidade],
        ufs=ufs,
        tamanho_pagina=config.page_size,
        prioridade=config.update_priority,
    )[0]


def _planejar_backfill(config: ConfiguracaoAgendamentoPncp, *, agora: datetime) -> list[NovoJob]:
    """Gera janelas históricas completas até o início da semana corrente.

    A fronteira semanal estável evita criar uma nova janela parcial a cada ciclo;
    publicacao/atualizacao recorrentes cobrem o período corrente.
    """
    inicio_semana = (agora - timedelta(days=agora.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cutoff = inicio_semana - timedelta(seconds=1)
    jobs: list[NovoJob] = []
    for ano in range(config.backfill_start_year, agora.year + 1):
        jobs.extend(agendar_backfill_anual(
            ano=ano,
            modalidades=list(config.modalidades),
            ufs=config.ufs,
            agora=cutoff,
            tamanho_janela_dias=config.backfill_window_days,
            tamanho_pagina=config.page_size,
        ))
    return [replace(job, priority=config.backfill_priority) for job in jobs]


def _cursor_ciclo(session: Session) -> PncpSyncCursor | None:
    return session.get(PncpSyncCursor, {
        "source": "PNCP", "entity_type": "CONTRATACAO",
        "flow": _CICLO_FLOW, "scope_key": _CICLO_SCOPE,
    })


def _vincular_ciclo(job: NovoJob, ciclo_id: str) -> NovoJob:
    return replace(
        job,
        dedupe_key=f"{job.dedupe_key}:ciclo:{ciclo_id}",
        parameters={**job.parameters, "scheduler_cycle_id": ciclo_id},
    )


def _jobs_ciclo(session: Session, *, config: ConfiguracaoAgendamentoPncp, agora: datetime) -> list[NovoJob]:
    jobs = [
        _planejar_job(
            session, flow=flow, modalidade=modalidade, uf=uf,
            agora=agora, config=config,
        )
        for flow in config.flows
        for uf in (config.ufs or (None,))
        for modalidade in config.modalidades
    ]
    return jobs


def _agendar_ciclo_session(
    session: Session,
    *,
    config: ConfiguracaoAgendamentoPncp,
    agora: datetime,
    incluir_backfill: bool,
) -> tuple[str, dict[str, int]]:
    ciclo_id = str(uuid4())
    jobs = [
        _vincular_ciclo(job, ciclo_id)
        for job in _jobs_ciclo(session, config=config, agora=agora)
    ]
    backfill_jobs = _planejar_backfill(config, agora=agora) if incluir_backfill and config.backfill_enabled else []
    jobs.extend(_vincular_ciclo(job, ciclo_id) for job in backfill_jobs)

    agendados = 0
    ja_ativos = 0
    existentes_ativos = session.scalars(
        select(IngestionJob).where(
            IngestionJob.source == "PNCP",
            IngestionJob.job_type.in_(("incremental", "backfill")),
            IngestionJob.status.in_(("PENDING", "RUNNING", "RETRY_WAIT")),
        ).with_for_update()
    ).all()
    por_escopo_ativo: dict[tuple[str, str], IngestionJob] = {}
    for existente in existentes_ativos:
        por_escopo_ativo.setdefault((existente.job_type, existente.scope_key), existente)
    for job in jobs:
        existente = por_escopo_ativo.get((job.job_type, job.scope_key))
        if existente is not None:
            existente.parameters = {
                **(existente.parameters or {}), "scheduler_cycle_id": ciclo_id,
            }
            ja_ativos += 1
            continue
        _, criado = agendar_job_session(session, job)
        agendados += int(criado)

    # Janelas históricas que esgotaram tentativas voltam a ser tentadas no ciclo
    # seguinte, com no máximo uma nova rodada a cada ciclo completo.
    reparos = session.execute(text("""
        WITH reparados AS (
          UPDATE ingestion_jobs
        SET status = 'PENDING', attempts = 0, locked_by = NULL, lease_token = NULL,
            lease_until = NULL, heartbeat_at = NULL, next_run_at = now(),
            replay_count = replay_count + 1, last_replayed_at = now(), updated_at = now(),
            parameters = parameters || CAST(:cycle_parameters AS jsonb)
        WHERE source = 'PNCP' AND job_type = 'backfill' AND status = 'FAILED'
          AND coalesce(last_error_context->>'retryable', 'false') = 'true'
        RETURNING id
        )
        INSERT INTO ingestion_job_events (job_id, event_type, details)
        SELECT id, 'REPLAYED', CAST(:event_details AS jsonb) FROM reparados
        RETURNING job_id
    """), {
        "cycle_parameters": json.dumps({"scheduler_cycle_id": ciclo_id}),
        "event_details": json.dumps({"source": "PNCP", "reason": "automatic_backfill_retry", "scheduler_cycle_id": ciclo_id}),
    }).all() if config.backfill_enabled and not incluir_backfill else []

    return ciclo_id, {
        "ciclo_jobs_avaliados": len(jobs),
        "ciclo_jobs_criados": agendados,
        "ciclo_jobs_existentes_associados": ja_ativos,
        "ciclo_backfill_reparados": len(reparos),
    }


def _ativos_ciclo(session: Session, ciclo_id: str) -> int:
    return int(session.scalar(
        select(func.count()).select_from(IngestionJob).where(
            IngestionJob.source == "PNCP",
            IngestionJob.parameters["scheduler_cycle_id"].as_string() == ciclo_id,
            IngestionJob.status.in_(("PENDING", "RUNNING", "RETRY_WAIT")),
        )
    ) or 0)


def agendar_fluxos_pncp_session(
    session: Session,
    *,
    config: ConfiguracaoAgendamentoPncp | None = None,
    agora: datetime | None = None,
) -> dict[str, int | str]:
    """Executa um ciclo completo e só abre outro após conclusão + intervalo."""
    config = config or ConfiguracaoAgendamentoPncp.do_ambiente()
    config.validar()
    instante = agora or datetime.now(timezone.utc)
    if instante.tzinfo is None:
        raise ValueError("agora deve incluir timezone")

    # Serializa schedulers concorrentes para que tambem jobs ja concluidos sejam
    # reconhecidos antes de inserir uma nova ocorrencia equivalente.
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _ADVISORY_LOCK_ID},
    )
    cursor = _cursor_ciclo(session)
    if cursor is None:
        cursor = PncpSyncCursor(
            source="PNCP", entity_type="CONTRATACAO", flow=_CICLO_FLOW,
            scope_key=_CICLO_SCOPE, status="IDLE",
        )
        session.add(cursor)
        session.flush()

    if cursor.status == "RUNNING" and cursor.last_run_id:
        ativos = _ativos_ciclo(session, cursor.last_run_id)
        if ativos:
            return {"ciclo_id": cursor.last_run_id, "ciclo_em_andamento": 1, "jobs_ativos": ativos}
        cursor.status = "IDLE"
        cursor.last_confirmed_end = instante
        cursor.last_run_id = None
        return {
            "ciclo_concluido": 1,
            "proxima_execucao_em": (instante + timedelta(seconds=config.cycle_interval_seconds)).isoformat(),
            "jobs_ativos": 0,
        }

    proxima_execucao = (
        cursor.last_confirmed_end + timedelta(seconds=config.cycle_interval_seconds)
        if cursor.last_confirmed_end else instante
    )
    if instante < proxima_execucao:
        return {"aguardando_proximo_ciclo": 1, "proxima_execucao_em": proxima_execucao.isoformat()}

    primeiro_ciclo = cursor.last_confirmed_end is None
    ciclo_id, resultado = _agendar_ciclo_session(
        session, config=config, agora=instante, incluir_backfill=primeiro_ciclo,
    )
    cursor.status = "RUNNING"
    cursor.last_run_id = ciclo_id
    cursor.updated_at = instante
    resultado["ciclo_id"] = ciclo_id
    resultado["ciclo_iniciado"] = 1
    return resultado


def agendar_fluxos_pncp(
    *,
    config: ConfiguracaoAgendamentoPncp | None = None,
    agora: datetime | None = None,
) -> dict[str, int | str]:
    with orm.main_session() as session:
        return agendar_fluxos_pncp_session(session, config=config, agora=agora)


__all__ = [
    "ConfiguracaoAgendamentoPncp",
    "agendar_fluxos_pncp",
    "agendar_fluxos_pncp_session",
]
