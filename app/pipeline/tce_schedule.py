"""Politica semanal e handlers dos jobs persistentes do TCE-CE."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core import database, orm
from app.core.config import (
    TCE_INCREMENTAL_OVERLAP_MONTHS,
    TCE_JOB_LEASE_SECONDS,
    TCE_SCHEDULE_HOUR,
    TCE_SCHEDULE_TIMEZONE,
    TCE_SCHEDULE_WEEKDAY,
    TCE_SCHEDULE_YEARS,
    UF_PADRAO,
)
from app.core.models import (
    IngestionJob,
    TceDespesaIngestionRun,
    TcePagamentoIngestionRun,
)
from app.pipeline.ingestion import ibge, tce
from app.pipeline.ingestion.fornecedores import sincronizar_fornecedores
from app.pipeline.persistence.tce_despesas import (
    STATUS_PUBLICADO as STATUS_DESPESA_PUBLICADO,
    listar_cnpjs_fornecedores_publicados,
)
from app.pipeline.persistence.tce_pagamentos import (
    STATUS_PUBLICADO as STATUS_PAGAMENTO_PUBLICADO,
)
from app.pipeline.referencias import referencias_entre
from app.pipeline.tce_despesas import executar_ingestao_competencia_tce
from app.pipeline.tce_job_queue import (
    ACTIVE_STATUSES,
    JobTceReivindicado,
    NovoJobTce,
    SOURCE,
    agendar_job_unico,
    agendar_lote_session,
    renovar_lease,
)
from app.pipeline.tce_pagamentos import executar_ingestao_competencia_pagamentos_tce
from app.utils import normalizar_cnpj


logger = logging.getLogger(__name__)
QUANTIDADE_MUNICIPIOS_CE = 184


class FalhaPermanenteJobTce(RuntimeError):
    retryable = False


class LoteTceAindaEmExecucao(RuntimeError):
    retryable = True
    reagendar_sem_tentativa = True
    retry_after = timedelta(minutes=15)


def referencia_semanal(
    agora: datetime,
    *,
    weekday: int = TCE_SCHEDULE_WEEKDAY,
    hour: int = TCE_SCHEDULE_HOUR,
    timezone_name: str = TCE_SCHEDULE_TIMEZONE,
) -> datetime:
    """Retorna o ultimo horario semanal devido, permitindo catch-up apos restart."""
    if agora.tzinfo is None:
        raise ValueError("agora deve incluir timezone")
    if not 0 <= weekday <= 6 or not 0 <= hour <= 23:
        raise ValueError("weekday ou hour invalido")
    fuso = ZoneInfo(timezone_name)
    local = agora.astimezone(fuso)
    dias = (local.weekday() - weekday) % 7
    data_referencia = local.date() - timedelta(days=dias)
    referencia = datetime.combine(data_referencia, time(hour=hour), tzinfo=fuso)
    if local < referencia:
        referencia -= timedelta(days=7)
    return referencia


def lote_id(referencia: datetime) -> str:
    return referencia.strftime("%Y%m%dT%H%M%S%z")


def _inicio_mes(competencia: str) -> datetime:
    return datetime.fromisoformat(f"{competencia}-01T00:00:00+00:00")


def _fim_mes(competencia: str) -> datetime:
    inicio = _inicio_mes(competencia)
    if inicio.month == 12:
        seguinte = inicio.replace(year=inicio.year + 1, month=1)
    else:
        seguinte = inicio.replace(month=inicio.month + 1)
    return seguinte - timedelta(microseconds=1)


def _competencias_despesas(ano: int, hoje: date) -> list[str]:
    if ano > hoje.year:
        return []
    fim = f"{ano:04d}-{(hoje.month if ano == hoje.year else 12):02d}"
    return referencias_entre(f"{ano:04d}-01", fim)


def _competencias_pagamentos(ano: int, hoje: date) -> list[str]:
    if ano > hoje.year:
        return []
    return referencias_entre(f"{ano:04d}-01", hoje.strftime("%Y-%m"))


def _competencias_incrementais(hoje: date, quantidade: int) -> set[str]:
    indice_final = hoje.year * 12 + hoje.month - 1
    return {
        f"{indice // 12:04d}-{indice % 12 + 1:02d}"
        for indice in range(indice_final - quantidade + 1, indice_final + 1)
    }


def produzir_jobs_lote(
    *,
    lote: str,
    codigos_municipios: list[str],
    anos: tuple[int, ...],
    hoje: date,
    despesas_publicadas: set[tuple[str, str]],
    pagamentos_publicados: set[tuple[str, int, str]],
    agora: datetime,
    overlap_meses: int = TCE_INCREMENTAL_OVERLAP_MONTHS,
) -> list[NovoJobTce]:
    """Produz backfill ausente e overlap recente sem consultar banco ou fonte."""
    recentes = _competencias_incrementais(hoje, overlap_meses)
    jobs: list[NovoJobTce] = []
    for codigo in sorted(set(codigos_municipios)):
        for ano in anos:
            for competencia in _competencias_despesas(ano, hoje):
                publicado = (codigo, competencia) in despesas_publicadas
                if publicado and competencia not in recentes:
                    continue
                prioridade = 90 if competencia in recentes else 50
                jobs.append(
                    NovoJobTce(
                        job_type="tce_despesas",
                        entity_type="DESPESA",
                        scope_key=f"despesas|{codigo}|{competencia}",
                        dedupe_key=f"tce:{lote}:despesas:{codigo}:{competencia}",
                        priority=prioridade,
                        next_run_at=agora,
                        window_start=_inicio_mes(competencia),
                        window_end=_fim_mes(competencia),
                    )
                )
            for competencia in _competencias_pagamentos(ano, hoje):
                publicado = (codigo, ano * 100, competencia) in pagamentos_publicados
                if publicado and competencia not in recentes:
                    continue
                prioridade = 80 if competencia in recentes else 40
                jobs.append(
                    NovoJobTce(
                        job_type="tce_pagamentos",
                        entity_type="PAGAMENTO",
                        scope_key=f"pagamentos|{codigo}|{ano}|{competencia}",
                        dedupe_key=(
                            f"tce:{lote}:pagamentos:{codigo}:{ano}:{competencia}"
                        ),
                        priority=prioridade,
                        next_run_at=agora,
                        window_start=_inicio_mes(competencia),
                        window_end=_fim_mes(competencia),
                        page=ano,
                    )
                )
    for ano in anos:
        jobs.append(
            NovoJobTce(
                job_type="tce_fornecedores",
                entity_type="FORNECEDOR",
                scope_key=f"fornecedores|{ano}|{lote}",
                dedupe_key=f"tce:{lote}:fornecedores:{ano}",
                priority=10,
                next_run_at=agora,
            )
        )
    return jobs


def agendar_execucao_semanal(
    referencia: datetime,
) -> tuple[int, bool]:
    """Agenda somente o catalogo; ele expande o lote apos validar os 184 municipios."""
    lote = lote_id(referencia)
    return agendar_job_unico(
        NovoJobTce(
            job_type="tce_catalogo",
            entity_type="MUNICIPIO",
            scope_key=f"catalogo|{lote}",
            dedupe_key=f"tce:{lote}:catalogo",
            priority=100,
            next_run_at=datetime.now(timezone.utc),
        )
    )


def _sincronizar_catalogo() -> list[str]:
    municipios_ibge = ibge.sincronizar_municipios(UF_PADRAO)
    codigos_ibge = {str(item["id"]) for item in municipios_ibge}
    municipios_tce = tce.buscar_municipios()
    municipios = [
        item
        for item in municipios_tce
        if str(item.get("codigo_municipio_ibge")) in codigos_ibge
    ]
    codigos = sorted({str(item.get("codigo_municipio") or "") for item in municipios} - {""})
    if (
        len(codigos_ibge) != QUANTIDADE_MUNICIPIOS_CE
        or len(municipios) != QUANTIDADE_MUNICIPIOS_CE
        or len(codigos) != QUANTIDADE_MUNICIPIOS_CE
    ):
        raise RuntimeError(
            "Catalogo municipal TCE/IBGE incompleto: "
            f"IBGE={len(codigos_ibge)}, TCE={len(municipios)}, "
            f"codigos={len(codigos)}; esperado={QUANTIDADE_MUNICIPIOS_CE}."
        )
    database.salvar_municipios_tce(municipios, UF_PADRAO)
    return codigos


def _publicacoes(
    codigos: list[str],
    anos: tuple[int, ...],
) -> tuple[set[tuple[str, str]], set[tuple[str, int, str]]]:
    with orm.main_session() as session:
        despesas = {
            (str(codigo), str(competencia))
            for codigo, competencia in session.execute(
                select(
                    TceDespesaIngestionRun.codigo_municipio_tce,
                    TceDespesaIngestionRun.competencia,
                ).where(
                    TceDespesaIngestionRun.codigo_municipio_tce.in_(codigos),
                    TceDespesaIngestionRun.status == STATUS_DESPESA_PUBLICADO,
                )
            )
        }
        pagamentos = {
            (str(codigo), int(exercicio), str(competencia))
            for codigo, exercicio, competencia in session.execute(
                select(
                    TcePagamentoIngestionRun.codigo_municipio_tce,
                    TcePagamentoIngestionRun.exercicio_orcamento,
                    TcePagamentoIngestionRun.competencia,
                ).where(
                    TcePagamentoIngestionRun.codigo_municipio_tce.in_(codigos),
                    TcePagamentoIngestionRun.exercicio_orcamento.in_(
                        [ano * 100 for ano in anos]
                    ),
                    TcePagamentoIngestionRun.status == STATUS_PAGAMENTO_PUBLICADO,
                )
            )
        }
    return despesas, pagamentos


def _executar_catalogo(job: JobTceReivindicado) -> None:
    _, lote = job.scope_key.split("|", maxsplit=1)
    codigos = _sincronizar_catalogo()
    despesas, pagamentos = _publicacoes(codigos, TCE_SCHEDULE_YEARS)
    agora = datetime.now(timezone.utc)
    jobs = produzir_jobs_lote(
        lote=lote,
        codigos_municipios=codigos,
        anos=TCE_SCHEDULE_YEARS,
        hoje=agora.astimezone(ZoneInfo(TCE_SCHEDULE_TIMEZONE)).date(),
        despesas_publicadas=despesas,
        pagamentos_publicados=pagamentos,
        agora=agora,
    )
    with orm.main_session() as session:
        criados, avaliados = agendar_lote_session(session, lote_id=lote, jobs=jobs)
    logger.info(
        "Lote semanal TCE expandido",
        extra={
            "lote": lote,
            "municipios": len(codigos),
            "anos": list(TCE_SCHEDULE_YEARS),
            "jobs_avaliados": avaliados,
            "jobs_criados": criados,
        },
    )


def _executar_despesas(job: JobTceReivindicado) -> None:
    _, codigo, competencia = job.scope_key.split("|")
    resultado = executar_ingestao_competencia_tce(
        competencia,
        codigo_municipio_tce=codigo,
    )
    if resultado.status != STATUS_DESPESA_PUBLICADO:
        raise FalhaPermanenteJobTce(
            f"Lote de despesas {codigo}/{competencia} terminou em {resultado.status}."
        )


def _executar_pagamentos(job: JobTceReivindicado) -> None:
    _, codigo, ano, competencia = job.scope_key.split("|")
    resultado = executar_ingestao_competencia_pagamentos_tce(
        competencia,
        exercicio_orcamento=int(ano),
        codigo_municipio_tce=codigo,
    )
    if resultado.status != STATUS_PAGAMENTO_PUBLICADO:
        raise FalhaPermanenteJobTce(
            f"Lote de pagamentos {codigo}/{ano}/{competencia} terminou em "
            f"{resultado.status}."
        )


def _jobs_ingestao_ativos(lote: str, job_id: int) -> int:
    with orm.main_session() as session:
        return int(
            session.scalar(
                select(func.count(IngestionJob.id)).where(
                    IngestionJob.source == SOURCE,
                    IngestionJob.id != job_id,
                    IngestionJob.dedupe_key.like(f"tce:{lote}:%"),
                    IngestionJob.job_type.in_(("tce_despesas", "tce_pagamentos")),
                    IngestionJob.status.in_(ACTIVE_STATUSES),
                )
            )
            or 0
        )


def _executar_fornecedores(job: JobTceReivindicado) -> None:
    _, ano, lote = job.scope_key.split("|")
    ativos = _jobs_ingestao_ativos(lote, job.id)
    if ativos:
        raise LoteTceAindaEmExecucao(
            f"Lote {lote} ainda possui {ativos} jobs de ingestao ativos."
        )
    ano_int = int(ano)
    cnpjs = listar_cnpjs_fornecedores_publicados(
        data_inicial=date(ano_int, 1, 1),
        data_final=date(ano_int, 12, 31),
        codigo_municipio_tce=None,
        uf=UF_PADRAO,
    )
    normalizados = sorted(
        {
            cnpj
            for valor in cnpjs
            if (cnpj := normalizar_cnpj(valor))
        }
    )
    for inicio in range(0, len(normalizados), 100):
        sincronizar_fornecedores(
            normalizados[inicio : inicio + 100],
            throttle_segundos=0.3,
        )
        if not renovar_lease(
            job_id=job.id,
            lease_token=job.lease_token,
            lease_seconds=TCE_JOB_LEASE_SECONDS,
        ):
            raise RuntimeError("Lease perdido durante enriquecimento de fornecedores.")


def executar_job(job: JobTceReivindicado) -> None:
    handlers = {
        "tce_catalogo": _executar_catalogo,
        "tce_despesas": _executar_despesas,
        "tce_pagamentos": _executar_pagamentos,
        "tce_fornecedores": _executar_fornecedores,
    }
    try:
        handler = handlers[job.job_type]
    except KeyError as error:
        raise FalhaPermanenteJobTce(
            f"Tipo de job TCE desconhecido: {job.job_type}."
        ) from error
    handler(job)


__all__ = [
    "FalhaPermanenteJobTce",
    "LoteTceAindaEmExecucao",
    "agendar_execucao_semanal",
    "executar_job",
    "lote_id",
    "produzir_jobs_lote",
    "referencia_semanal",
]
