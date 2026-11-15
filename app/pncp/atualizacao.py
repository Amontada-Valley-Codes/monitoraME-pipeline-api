"""Fluxo independente de atualizacoes globais do PNCP."""
from __future__ import annotations
from datetime import datetime, timezone
import logging
import time
from typing import Any
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core import orm
from app.core.models import PncpContratacaoAtual
from app.pncp.ingestion import (
    ClientePncp,
    MODALIDADE_PREGAO_ELETRONICO,
    PncpColetaError,
    UFS_NORDESTE,
    data_atualizacao_fonte,
    tamanho_pagina_do_job,
)
from app.pncp.jobs import JobReivindicado, NovoJob, PNCP_QUERY_CONFIG_VERSION
from app.pncp.raw import (
    RegistroRawPncp,
    identificar_contratacao_raw,
    salvar_raw_e_agendar_normalizacao_session,
)
from app.pncp.checkpoints import ChaveCursor, confirmar_janela_job

FLOW = "atualizacao"
logger = logging.getLogger(__name__)

def _data(valor: Any) -> datetime | None:
    return data_atualizacao_fonte(valor)

def agendar_atualizacao(
    *,
    inicio: datetime,
    fim: datetime,
    modalidades: list[int] | None = None,
    ufs: tuple[str, ...] | list[str] | None = None,
    tamanho_pagina: int = 50,
    prioridade: int = 80,
) -> list[NovoJob]:
    if inicio.tzinfo is None or fim.tzinfo is None or fim < inicio:
        raise ValueError("janela com timezone e ordem valida e obrigatoria")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    if ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)):
        raise ValueError("ufs deve conter somente UFs do Nordeste")
    jobs: list[NovoJob] = []
    codigos_modalidade = modalidades or [MODALIDADE_PREGAO_ELETRONICO]
    for uf in (ufs or (None,)):
        for modalidade in codigos_modalidade:
            uf_scope = f":uf:{uf}" if uf is not None else ""
            scope = (
                f"atualizacao:{inicio.date().isoformat()}:{fim.date().isoformat()}"
                f"{uf_scope}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
            )
            dedupe_key = f"{scope}:config:{PNCP_QUERY_CONFIG_VERSION}"
            jobs.append(NovoJob(
                "incremental",
                "CONTRATACAO",
                "PNCP",
                scope,
                dedupe_key,
                prioridade,
                datetime.now(timezone.utc),
                window_start=inicio,
                window_end=fim,
                page=1,
                parameters={
                    "flow": FLOW,
                    "codigoModalidadeContratacao": modalidade,
                    "uf": uf,
                    "tamanhoPagina": tamanho_pagina,
                },
                config_version=PNCP_QUERY_CONFIG_VERSION,
            ))
    return jobs

def salvar_versao_atual_session(session: Session, item: dict[str, Any], payload_hash: str, source_updated_at: datetime | None) -> str:
    external_id = str(item["numeroControlePNCP"]).strip()
    anterior = session.get(PncpContratacaoAtual, {"source": "PNCP", "external_id": external_id})
    if anterior and anterior.payload_hash == payload_hash: return "INALTERADO"
    if anterior and anterior.source_updated_at and (source_updated_at is None or source_updated_at < anterior.source_updated_at): return "ANTIGO"
    session.execute(insert(PncpContratacaoAtual).values(source="PNCP", external_id=external_id, payload_hash=payload_hash, source_updated_at=source_updated_at, payload=item).on_conflict_do_update(index_elements=["source", "external_id"], set_={"payload_hash": payload_hash, "source_updated_at": source_updated_at, "payload": item, "updated_at": text("now()")}))
    return "NOVO" if anterior is None else "ALTERADO"

def coletar_atualizacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 50) -> dict[str, int]:
    if (
        job.job_type != "incremental"
        or job.source != "PNCP"
        or job.entity_type != "CONTRATACAO"
        or not job.scope_key.startswith("atualizacao:")
        or job.window_start is None
        or job.window_end is None
        or job.window_start.tzinfo is None
        or job.window_end.tzinfo is None
        or job.window_end < job.window_start
    ):
        raise ValueError("job nao representa uma janela valida de atualizacao PNCP")
    tamanho_pagina = tamanho_pagina_do_job(job, tamanho_pagina)
    try:
        modalidade = int(job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0])
        uf = job.scope_key.split(":uf:", 1)[1].split(":", 1)[0] if ":uf:" in job.scope_key else None
    except (IndexError, ValueError) as exc:
        raise ValueError("scope_key de atualizacao invalido") from exc
    pagina = job.page if job.page is not None else 1
    if isinstance(pagina, bool) or pagina != 1:
        raise ValueError("coleta por janela deve reiniciar na pagina 1")
    totais = {"NOVO": 0, "ALTERADO": 0, "INALTERADO": 0}
    paginas_lidas = 0
    registros_acumulados = 0
    raw_criados = 0
    raw_reutilizados = 0
    normalizacoes_agendadas = 0
    normalizacoes_reutilizadas = 0
    sem_numero_controle = 0
    while True:
        inicio_pagina = time.monotonic()
        resposta = cliente.buscar_atualizacoes(inicio=job.window_start.date(), fim=job.window_end.date(), modalidade=modalidade, uf=uf, pagina=pagina, tamanho_pagina=tamanho_pagina)
        if not resposta.itens and resposta.total_paginas:
            raise PncpColetaError(
                "pagina de atualizacao incompleta",
                endpoint="/v1/contratacoes/atualizacao",
                page=pagina,
            )
        registros_pagina = len(resposta.itens)
        raw_criados_pagina = 0
        raw_reutilizados_pagina = 0
        normalizacoes_agendadas_pagina = 0
        normalizacoes_reutilizadas_pagina = 0
        sem_numero_controle_pagina = 0
        versoes_antigas_pagina = 0
        totais_pagina = {"NOVO": 0, "ALTERADO": 0, "INALTERADO": 0}
        for item in resposta.itens:
            external_id, tem_numero_controle = identificar_contratacao_raw(item)
            atualizado = _data(item.get("dataAtualizacao"))
            with orm.main_session() as s:
                persistencia = salvar_raw_e_agendar_normalizacao_session(
                    s,
                    RegistroRawPncp(
                        "CONTRATACAO", external_id, item, datetime.now(timezone.utc),
                        atualizado, f"job:{job.id}:pagina:{pagina}",
                    ),
                )
                classe = (
                    salvar_versao_atual_session(s, item, persistencia.payload_hash, atualizado)
                    if tem_numero_controle
                    else None
                )
            raw_criados_pagina += int(persistencia.raw_criado)
            raw_reutilizados_pagina += int(not persistencia.raw_criado)
            normalizacoes_agendadas_pagina += int(persistencia.job_criado)
            normalizacoes_reutilizadas_pagina += int(not persistencia.job_criado)
            if not tem_numero_controle:
                sem_numero_controle_pagina += 1
            elif classe == "ANTIGO":
                versoes_antigas_pagina += 1
            if classe is not None and classe != "ANTIGO":
                totais[classe] += 1
                totais_pagina[classe] += 1
        paginas_lidas += 1
        registros_acumulados += registros_pagina
        raw_criados += raw_criados_pagina
        raw_reutilizados += raw_reutilizados_pagina
        normalizacoes_agendadas += normalizacoes_agendadas_pagina
        normalizacoes_reutilizadas += normalizacoes_reutilizadas_pagina
        sem_numero_controle += sem_numero_controle_pagina
        logger.info(
            "Pagina PNCP de atualizacoes processada",
            extra={
                "job_id": job.id,
                "flow": FLOW,
                "uf": uf,
                "modalidade": modalidade,
                "window_start": job.window_start,
                "window_end": job.window_end,
                "pagina": pagina,
                "paginas_totais": resposta.total_paginas,
                "paginas_restantes": (
                    max(0, resposta.total_paginas - pagina)
                    if resposta.total_paginas is not None else None
                ),
                "registros_pagina": registros_pagina,
                "registros_acumulados": registros_acumulados,
                "classificacao_pagina": totais_pagina,
                "raw_criados_pagina": raw_criados_pagina,
                "raw_reutilizados_pagina": raw_reutilizados_pagina,
                "jobs_normalizacao_criados_pagina": normalizacoes_agendadas_pagina,
                "jobs_normalizacao_reutilizados_pagina": normalizacoes_reutilizadas_pagina,
                "sem_numero_controle_pagina": sem_numero_controle_pagina,
                "versoes_antigas_pagina": versoes_antigas_pagina,
                "duracao_pagina_segundos": round(time.monotonic() - inicio_pagina, 3),
            },
        )
        if resposta.total_paginas is not None and pagina >= resposta.total_paginas: break
        if resposta.total_paginas is None and len(resposta.itens) < tamanho_pagina: break
        pagina += 1
    escopo_cursor = f"uf:{uf}:modalidade:{modalidade}" if uf else f"modalidade:{modalidade}"
    confirmar_janela_job(
        ChaveCursor("PNCP", "CONTRATACAO", FLOW, escopo_cursor),
        final=job.window_end,
        job_id=job.id,
        lease_token=job.lease_token,
    )
    logger.info(
        "Coleta PNCP de atualizacoes concluida",
        extra={
            "job_id": job.id,
            "flow": FLOW,
            "uf": uf,
            "modalidade": modalidade,
            "window_start": job.window_start,
            "window_end": job.window_end,
            "paginas_lidas": paginas_lidas,
            "registros_recebidos": registros_acumulados,
            "classificacao_total": totais,
            "raw_criados": raw_criados,
            "raw_reutilizados": raw_reutilizados,
            "jobs_normalizacao_criados": normalizacoes_agendadas,
            "jobs_normalizacao_reutilizados": normalizacoes_reutilizadas,
            "sem_numero_controle": sem_numero_controle,
            "cursor_confirmado": True,
        },
    )
    return totais
