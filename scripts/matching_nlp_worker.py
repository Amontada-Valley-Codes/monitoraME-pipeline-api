"""Entry point do worker NLP de embeddings e batches de matching."""
from __future__ import annotations

import logging
import os

from app.nlp.embeddings.worker import executar_worker


def main() -> int:
    nivel = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    executar_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
