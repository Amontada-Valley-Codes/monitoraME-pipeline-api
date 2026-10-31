"""Workers persistentes de coleta e normalizacao PNCP."""
from __future__ import annotations

import argparse
import os
import time
from typing import Any

from app.pncp.atualizacao import coletar_atualizacao
from app.pncp.ingestion import ClientePncp, coletar_publicacao
from app.pncp.jobs import WorkerPncp, recuperar_leases_expirados
from app.pncp.normalization import normalizar_job


def executar_coleta() -> None:
    cliente = ClientePncp()

    def processar(job: Any) -> None:
        if job.scope_key.startswith(("publicacao:", "backfill:")):
            coletar_publicacao(job, cliente)
        elif job.scope_key.startswith("atualizacao:"):
            coletar_atualizacao(job, cliente)
        else:
            raise ValueError(f"fluxo PNCP desconhecido: {job.scope_key}")

    workers = (
        WorkerPncp("pncp-worker-incremental", processar, job_type="incremental"),
        WorkerPncp("pncp-worker-backfill", processar, job_type="backfill"),
    )
    intervalo_ocioso = float(os.getenv("PNCP_WORKER_IDLE_SECONDS", "1"))
    ultimo_recovery = 0.0
    while True:
        if time.monotonic() - ultimo_recovery >= 30:
            recuperar_leases_expirados()
            ultimo_recovery = time.monotonic()
        houve_trabalho = False
        for worker in workers:
            houve_trabalho = worker.executar_uma_vez() or houve_trabalho
        if not houve_trabalho:
            time.sleep(intervalo_ocioso)


def executar_normalizacao() -> None:
    worker = WorkerPncp(
        "pncp-worker-normalizacao",
        normalizar_job,
        job_type="normalize",
    )
    intervalo_ocioso = float(os.getenv("PNCP_WORKER_IDLE_SECONDS", "1"))
    ultimo_recovery = 0.0
    while True:
        if time.monotonic() - ultimo_recovery >= 30:
            recuperar_leases_expirados()
            ultimo_recovery = time.monotonic()
        if not worker.executar_uma_vez():
            time.sleep(intervalo_ocioso)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tipo", choices=("coleta", "normalizacao"))
    tipo = parser.parse_args().tipo
    if tipo == "coleta":
        executar_coleta()
    else:
        executar_normalizacao()


if __name__ == "__main__":
    main()
