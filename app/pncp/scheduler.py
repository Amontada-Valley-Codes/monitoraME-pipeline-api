"""Cria janelas recorrentes PNCP na fila PostgreSQL, sem executar coleta."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import IngestionJob, PncpSyncCursor
from app.pncp.ingestion import (
    MODALIDADES_PNCP_PADRAO,
    UFS_NORDESTE,
    agendar_propostas_abertas,
)
from app.pncp.jobs import NovoJob, agendar_job_session


_ADVISORY_LOCK_ID = 0x504E435053434844
_FLOWS_VALIDOS = frozenset(("propostas_abertas",))
_CICLO_FLOW = "scheduler"
_CICLO_SCOPE = "completo"
_CICLO_INTERVALO_PADRAO = 4 * 60 * 60
_HORIZONTE_PROPOSTAS_ABERTAS_DIAS_PADRAO = 410


@dataclass(frozen=True)
class ConfiguracaoAgendamentoPncp:
    flows: tuple[str, ...] = ("propostas_abertas",)
    modalidades: tuple[int, ...] = MODALIDADES_PNCP_PADRAO
    ufs: tuple[str, ...] = UFS_NORDESTE
    page_size: int = 50
    open_proposals_priority: int = 70
    open_proposals_horizon_days: int = _HORIZONTE_PROPOSTAS_ABERTAS_DIAS_PADRAO
    cycle_interval_seconds: int = _CICLO_INTERVALO_PADRAO

    @classmethod
    def do_ambiente(cls) -> ConfiguracaoAgendamentoPncp:
        flows = _lista_texto("PNCP_SCHEDULER_FLOWS", "propostas_abertas")
        invalidos = set(flows) - _FLOWS_VALIDOS
        if invalidos:
            raise ValueError(f"fluxos PNCP nao suportados: {', '.join(sorted(invalidos))}")
        modalidades = _lista_inteiros(
            "PNCP_SCHEDULER_MODALIDADES",
            ",".join(str(codigo) for codigo in MODALIDADES_PNCP_PADRAO),
        )
        ufs_padrao = ",".join(UFS_NORDESTE)
        ufs_configuradas = os.getenv("PNCP_SCHEDULER_UFS", ufs_padrao).strip()
        ufs = UFS_NORDESTE if ufs_configuradas == "*" else tuple(
            uf.upper() for uf in _lista_texto("PNCP_SCHEDULER_UFS", ufs_padrao)
        )
        if not ufs or any(uf not in UFS_NORDESTE for uf in ufs):
            raise ValueError("PNCP_SCHEDULER_UFS deve conter UFs suportadas ou *")
        config = cls(
            flows=flows,
            modalidades=modalidades,
            ufs=ufs,
            page_size=_inteiro_positivo("PNCP_SCHEDULER_PAGE_SIZE", 50),
            open_proposals_priority=int(os.getenv("PNCP_SCHEDULER_OPEN_PROPOSALS_PRIORITY", "70")),
            open_proposals_horizon_days=_inteiro_positivo(
                "PNCP_SCHEDULER_OPEN_PROPOSALS_HORIZON_DAYS",
                _HORIZONTE_PROPOSTAS_ABERTAS_DIAS_PADRAO,
            ),
            cycle_interval_seconds=_inteiro_positivo("PNCP_SCHEDULER_CYCLE_INTERVAL_SECONDS", _CICLO_INTERVALO_PADRAO),
        )
        config.validar()
        return config

    def validar(self) -> None:
        if not self.flows or set(self.flows) - _FLOWS_VALIDOS:
            raise ValueError("flows deve ser somente propostas_abertas")
        if not self.modalidades or any(codigo < 1 for codigo in self.modalidades):
            raise ValueError("modalidades deve conter codigos positivos")
        if not self.ufs or any(uf not in UFS_NORDESTE for uf in self.ufs):
            raise ValueError("ufs deve conter UFs suportadas")
        if not 1 <= self.page_size <= 50:
            raise ValueError("page_size deve estar entre 1 e 50")
        if self.open_proposals_horizon_days < 1:
            raise ValueError("open_proposals_horizon_days deve ser positivo")
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
    del session  # Propostas abertas não usam cursor histórico.
    agora_fortaleza = agora.astimezone(ZoneInfo("America/Fortaleza"))
    return agendar_propostas_abertas(
        data_final=agora_fortaleza + timedelta(days=config.open_proposals_horizon_days),
        modalidades=list(config.modalidades),
        ufs=config.ufs,
        tamanho_pagina=config.page_size,
        prioridade=config.open_proposals_priority,
    )


def _agendar_ciclo_session(
    session: Session,
    *,
    config: ConfiguracaoAgendamentoPncp,
    agora: datetime,
) -> tuple[str, dict[str, int]]:
    ciclo_id = str(uuid4())
    jobs = [
        _vincular_ciclo(job, ciclo_id)
        for job in _jobs_ciclo(session, config=config, agora=agora)
    ]
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

    return ciclo_id, {
        "ciclo_jobs_avaliados": len(jobs),
        "ciclo_jobs_criados": agendados,
        "ciclo_jobs_existentes_associados": ja_ativos,
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

    ciclo_id, resultado = _agendar_ciclo_session(
        session, config=config, agora=instante,
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
    "MODALIDADES_PNCP_PADRAO",
    "agendar_fluxos_pncp",
    "agendar_fluxos_pncp_session",
]
