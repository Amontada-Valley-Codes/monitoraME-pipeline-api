"""Cria janelas recorrentes PNCP na fila PostgreSQL, sem executar coleta."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import IngestionJob, PncpSyncCursor
from app.pncp.atualizacao import agendar_atualizacao
from app.pncp.checkpoints import ChaveCursor, OVERLAP_PADRAO, proxima_janela
from app.pncp.ingestion import (
    MODALIDADES_PRIORITARIAS,
    UFS_NORDESTE,
    agendar_publicacao,
)
from app.pncp.jobs import NovoJob, agendar_job_session


_ADVISORY_LOCK_ID = 0x504E435053434844
_FLOWS_VALIDOS = frozenset(("publicacao", "atualizacao"))


@dataclass(frozen=True)
class ConfiguracaoAgendamentoPncp:
    flows: tuple[str, ...] = ("publicacao", "atualizacao")
    modalidades: tuple[int, ...] = MODALIDADES_PRIORITARIAS
    ufs: tuple[str, ...] | None = UFS_NORDESTE
    window_days: int = 1
    page_size: int = 50
    publication_priority: int = 70
    update_priority: int = 80

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


def agendar_fluxos_pncp_session(
    session: Session,
    *,
    config: ConfiguracaoAgendamentoPncp | None = None,
    agora: datetime | None = None,
) -> dict[str, int]:
    """Agenda uma janela por fluxo/dimensao; repeticoes conservam o mesmo job."""
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
    jobs = [
        _planejar_job(
            session,
            flow=flow,
            modalidade=modalidade,
            uf=uf,
            agora=instante,
            config=config,
        )
        for flow in config.flows
        for uf in (config.ufs or (None,))
        for modalidade in config.modalidades
    ]
    dedupe_keys = {job.dedupe_key for job in jobs}
    existentes = set(session.scalars(
        select(IngestionJob.dedupe_key).where(IngestionJob.dedupe_key.in_(dedupe_keys))
    )) if dedupe_keys else set()

    resultado = {"avaliados": len(jobs), "criados": 0, "ja_existentes": 0}
    for job in jobs:
        if job.dedupe_key in existentes:
            resultado["ja_existentes"] += 1
            continue
        _, criado = agendar_job_session(session, job)
        resultado["criados" if criado else "ja_existentes"] += 1
    return resultado


def agendar_fluxos_pncp(
    *,
    config: ConfiguracaoAgendamentoPncp | None = None,
    agora: datetime | None = None,
) -> dict[str, int]:
    with orm.main_session() as session:
        return agendar_fluxos_pncp_session(session, config=config, agora=agora)


__all__ = [
    "ConfiguracaoAgendamentoPncp",
    "agendar_fluxos_pncp",
    "agendar_fluxos_pncp_session",
]
