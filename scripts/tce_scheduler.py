#!/usr/bin/env python3
"""Agenda o lote semanal persistente de ingestao do TCE-CE."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import signal
from threading import Event

from app.core.config import TCE_SCHEDULER_INTERVAL_SECONDS
from app.core.logging import configurar_logging
from app.pipeline.tce_schedule import agendar_execucao_semanal, referencia_semanal


logger = logging.getLogger("app.tce_scheduler")


def executar_uma_vez() -> tuple[int, bool]:
    referencia = referencia_semanal(datetime.now(timezone.utc))
    job_id, criado = agendar_execucao_semanal(referencia)
    logger.info(
        "Agendamento semanal TCE avaliado",
        extra={
            "job_id": job_id,
            "criado": criado,
            "referencia": referencia,
        },
    )
    return job_id, criado


def executar_loop(parar: Event) -> None:
    while not parar.is_set():
        try:
            executar_uma_vez()
        except Exception:
            logger.exception("Falha ao avaliar o agendamento semanal TCE")
        parar.wait(TCE_SCHEDULER_INTERVAL_SECONDS)


def main() -> None:
    configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="avalia o horario uma vez e encerra",
    )
    argumentos = parser.parse_args()
    if argumentos.once:
        executar_uma_vez()
        return
    parar = Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    executar_loop(parar)


if __name__ == "__main__":
    main()
