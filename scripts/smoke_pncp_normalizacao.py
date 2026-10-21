"""Normaliza um lote de RAW PNCP já persistido, sem acessar a fonte externa.

Uso no container:
python scripts/smoke_pncp_normalizacao.py --limite 20
"""
from __future__ import annotations

import argparse

from app.pncp.jobs import WorkerPncp
from app.pncp.normalization import normalizar_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=20)
    parser.add_argument("--worker-id", default="smoke-pncp-normalizacao")
    args = parser.parse_args()
    if args.limite < 1:
        parser.error("--limite deve ser maior que zero")

    worker = WorkerPncp(args.worker_id, normalizar_job, job_type="normalize")
    executados = 0
    for _ in range(args.limite):
        if not worker.executar_uma_vez():
            break
        executados += 1
    print({"executados": executados, "limite": args.limite})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
