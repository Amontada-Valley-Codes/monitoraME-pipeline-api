#!/usr/bin/env python3
"""Consulta a fila TCE ou autoriza o replay de um job com falha."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from app.core import orm
from app.pipeline.tce_job_queue import SOURCE, replay_job


def status() -> int:
    with orm.main_session() as session:
        linhas = session.execute(
            text("""
            SELECT job_type, status, count(*) AS total,
                   min(next_run_at) AS proxima_execucao_em
            FROM ingestion_jobs
            WHERE source = :source
            GROUP BY job_type, status
            ORDER BY job_type, status
            """),
            {"source": SOURCE},
        ).mappings().all()
    payload = [
        {
            **dict(linha),
            "proxima_execucao_em": (
                linha["proxima_execucao_em"].isoformat()
                if linha["proxima_execucao_em"] is not None
                else None
            ),
        }
        for linha in linhas
    ]
    print(json.dumps({"source": SOURCE, "jobs": payload}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcomandos = parser.add_subparsers(dest="comando", required=True)
    subcomandos.add_parser("status", help="resume a fila por tipo e estado")
    replay = subcomandos.add_parser("replay", help="reativa um job FAILED")
    replay.add_argument("--job-id", type=int, required=True)
    argumentos = parser.parse_args()
    if argumentos.comando == "status":
        return status()
    reagendado = replay_job(job_id=argumentos.job_id)
    print(json.dumps({"job_id": argumentos.job_id, "reagendado": reagendado}))
    return 0 if reagendado else 2


if __name__ == "__main__":
    raise SystemExit(main())
