"""Entry point do worker NLP de embeddings e batches de matching."""
from __future__ import annotations

import logging
import os
import argparse

from app.nlp.embeddings.worker import executar_worker


def main() -> int:
    nivel = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drain", action="store_true",
        help="Processa batches prontos e encerra após período ocioso; requer batch previamente enfileirado.",
    )
    parser.add_argument(
        "--idle-exit-seconds", type=float,
        default=float(os.getenv("NLP_WORKER_IDLE_EXIT_SECONDS", "5")),
    )
    args = parser.parse_args()
    executar_worker(drain=args.drain, idle_exit_seconds=args.idle_exit_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
