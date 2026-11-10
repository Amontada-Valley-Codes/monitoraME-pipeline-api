"""Workers persistentes de coleta e normalizacao PNCP."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import socket
import time
from typing import Any

from app.pncp.atualizacao import coletar_atualizacao
from app.pncp.ingestion import ClientePncp, coletar_publicacao
from app.pncp.jobs import WorkerPncp, recuperar_leases_expirados
from app.pncp.normalization import normalizar_job
from app.pncp.enrichment import ClienteEnriquecimentoPncp, agendar_historicos_pendentes, enriquecer_job
from app.core.logging import configurar_logging

logger = logging.getLogger("app.pncp.worker")


def recuperar_e_registrar_leases() -> int:
    recuperados = recuperar_leases_expirados(
        source="PNCP",
        job_types=("incremental", "backfill", "normalize", "enrich"),
    )
    if recuperados:
        logger.warning(
            "Leases PNCP expirados recuperados",
            extra={"jobs_recuperados": recuperados, "source": "PNCP"},
        )
    return recuperados


def executar_coleta() -> None:
    cliente = ClientePncp()
    worker_identity = f"{socket.gethostname()}:{os.getpid()}"

    def processar(job: Any) -> None:
        if job.scope_key.startswith(("publicacao:", "backfill:")):
            coletar_publicacao(job, cliente)
        elif job.scope_key.startswith("atualizacao:"):
            coletar_atualizacao(job, cliente)
        else:
            raise ValueError(f"fluxo PNCP desconhecido: {job.scope_key}")

    workers = (
        WorkerPncp(f"pncp-worker-incremental:{worker_identity}", processar, job_type="incremental"),
        WorkerPncp(f"pncp-worker-backfill:{worker_identity}", processar, job_type="backfill"),
    )
    intervalo_ocioso = float(os.getenv("PNCP_WORKER_IDLE_SECONDS", "1"))
    ultimo_recovery = time.monotonic() - 30
    while True:
        if time.monotonic() - ultimo_recovery >= 30:
            recuperar_e_registrar_leases()
            ultimo_recovery = time.monotonic()
        houve_trabalho = False
        for worker in workers:
            houve_trabalho = worker.executar_uma_vez() or houve_trabalho
        if not houve_trabalho:
            time.sleep(intervalo_ocioso)


def executar_normalizacao() -> None:
    worker = WorkerPncp(
        f"pncp-worker-normalizacao:{socket.gethostname()}:{os.getpid()}",
        normalizar_job,
        job_type="normalize",
    )
    intervalo_ocioso = float(os.getenv("PNCP_WORKER_IDLE_SECONDS", "1"))
    ultimo_recovery = time.monotonic() - 30
    while True:
        if time.monotonic() - ultimo_recovery >= 30:
            recuperar_e_registrar_leases()
            ultimo_recovery = time.monotonic()
        if not worker.executar_uma_vez():
            time.sleep(intervalo_ocioso)


def executar_enriquecimento() -> None:
    quantidade = int(os.getenv("PNCP_ENRICHMENT_WORKERS", "3"))
    if not 1 <= quantidade <= 5:
        raise ValueError("PNCP_ENRICHMENT_WORKERS deve estar entre 1 e 5")
    intervalo_ocioso = float(os.getenv("PNCP_WORKER_IDLE_SECONDS", "1"))

    def executar_slot(indice: int) -> None:
        # Cada slot mantém sua própria sessão HTTP; o limitador PNCP continua
        # compartilhado no PostgreSQL e impõe o teto global de taxa/concorrência.
        cliente = ClienteEnriquecimentoPncp()
        worker = WorkerPncp(
            f"pncp-worker-enriquecimento-{indice}-{socket.gethostname()}-{os.getpid()}",
            lambda job: enriquecer_job(job, cliente),
            job_type="enrich", lease_seconds=1800,
        )
        ultimo_recovery = time.monotonic() - 30
        intervalo_agendamento = max(5.0, float(os.getenv("PNCP_ENRICHMENT_SCHEDULE_INTERVAL_SECONDS", "30")))
        ultimo_agendamento = time.monotonic() - intervalo_agendamento
        while True:
            try:
                if time.monotonic() - ultimo_recovery >= 30:
                    recuperar_e_registrar_leases()
                    ultimo_recovery = time.monotonic()
                if time.monotonic() - ultimo_agendamento >= intervalo_agendamento:
                    jobs_historico = agendar_historicos_pendentes()
                    if jobs_historico:
                        logger.info(
                            "Históricos PNCP pendentes agendados automaticamente",
                            extra={"jobs_agendados": jobs_historico, "slot": indice},
                        )
                    ultimo_agendamento = time.monotonic()
                if not worker.executar_uma_vez():
                    time.sleep(intervalo_ocioso)
            except Exception:
                logger.exception("Falha no slot do worker de enriquecimento", extra={"slot": indice})
                time.sleep(intervalo_ocioso)

    with ThreadPoolExecutor(max_workers=quantidade, thread_name_prefix="pncp-enrich") as pool:
        futures = [pool.submit(executar_slot, indice) for indice in range(1, quantidade + 1)]
        for future in futures:
            future.result()


def main() -> None:
    configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tipo", choices=("coleta", "normalizacao", "enriquecimento"))
    tipo = parser.parse_args().tipo
    if tipo == "coleta":
        executar_coleta()
    elif tipo == "normalizacao":
        executar_normalizacao()
    else:
        executar_enriquecimento()


if __name__ == "__main__":
    main()
