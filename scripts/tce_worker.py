#!/usr/bin/env python3
"""Executa os jobs persistentes de ingestao e enriquecimento do TCE-CE."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import signal
import socket
from threading import Event
import time

from app.core.config import (
    TCE_JOB_LEASE_SECONDS,
    TCE_WORKER_CONCURRENCY,
    TCE_WORKER_IDLE_SECONDS,
)
from app.core.logging import configurar_logging
from app.pipeline.tce_job_queue import WorkerTce, recuperar_leases_expirados
from app.pipeline.tce_schedule import executar_job


logger = logging.getLogger("app.tce_worker")


def _executar_slot(indice: int, parar: Event, *, once: bool) -> None:
    worker = WorkerTce(
        f"tce-worker-{indice}-{socket.gethostname()}",
        executar_job,
        lease_seconds=TCE_JOB_LEASE_SECONDS,
    )
    ultimo_recovery = 0.0
    while not parar.is_set():
        try:
            if indice == 1 and time.monotonic() - ultimo_recovery >= 30:
                recuperados = recuperar_leases_expirados()
                ultimo_recovery = time.monotonic()
                if recuperados:
                    logger.warning(
                        "Leases TCE expirados foram recuperados",
                        extra={"quantidade": recuperados},
                    )
            houve_trabalho = worker.executar_uma_vez()
        except Exception:
            logger.exception(
                "Falha no loop do worker TCE",
                extra={"slot": indice},
            )
            houve_trabalho = False
        if once:
            return
        if not houve_trabalho:
            parar.wait(TCE_WORKER_IDLE_SECONDS)


def executar_workers(*, once: bool = False) -> None:
    parar = Event()
    if not once:
        signal.signal(signal.SIGTERM, lambda *_: parar.set())
        signal.signal(signal.SIGINT, lambda *_: parar.set())
    with ThreadPoolExecutor(
        max_workers=TCE_WORKER_CONCURRENCY,
        thread_name_prefix="tce-worker",
    ) as pool:
        futuros = [
            pool.submit(_executar_slot, indice, parar, once=once)
            for indice in range(1, TCE_WORKER_CONCURRENCY + 1)
        ]
        for futuro in futuros:
            futuro.result()


def main() -> None:
    configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="cada slot tenta executar no maximo um job e encerra",
    )
    executar_workers(once=parser.parse_args().once)


if __name__ == "__main__":
    main()
