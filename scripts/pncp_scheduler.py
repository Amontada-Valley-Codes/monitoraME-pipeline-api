"""Coordena ciclos PNCP persistentes sem consultar a API da fonte."""
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import configurar_logging
from app.pncp.scheduler import ConfiguracaoAgendamentoPncp, agendar_fluxos_pncp


logger = logging.getLogger("app.pncp.scheduler")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="avalia o ciclo atual uma vez e encerra")
    args = parser.parse_args()
    intervalo = int(os.getenv("PNCP_SCHEDULER_POLL_SECONDS", "30"))
    if intervalo < 5:
        parser.error("PNCP_SCHEDULER_POLL_SECONDS deve ser pelo menos 5")
    config = ConfiguracaoAgendamentoPncp.do_ambiente()
    configurar_logging()

    while True:
        try:
            resultado = agendar_fluxos_pncp(config=config)
            logger.info(
                "Estado do agendamento PNCP atualizado",
                extra={
                    **resultado,
                    "flows": config.flows,
                    "modalidades": config.modalidades,
                    "ufs": config.ufs,
                    "page_size": config.page_size,
                    "open_proposals_priority": config.open_proposals_priority,
                    "cycle_interval_seconds": config.cycle_interval_seconds,
                },
            )
        except Exception:
            logger.exception("Falha no ciclo de agendamento PNCP")
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(intervalo)


if __name__ == "__main__":
    raise SystemExit(main())
