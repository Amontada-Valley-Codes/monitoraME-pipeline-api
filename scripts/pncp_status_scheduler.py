"""Reavalia estados temporais a partir do banco local, sem consultar PNCP."""
from __future__ import annotations

import argparse
import logging
import time

from app.core.logging import configurar_logging
from app.pncp.derived_status import reavaliar_status_persistidos

logger = logging.getLogger("app.pncp.status_scheduler")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    configurar_logging()
    while True:
        alterados = reavaliar_status_persistidos()
        logger.info("Status PNCP reavaliados", extra={"alterados": alterados})
        if args.once:
            return 0
        time.sleep(max(5, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
