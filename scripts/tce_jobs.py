#!/usr/bin/env python3
"""Consulta a fila TCE ou autoriza o replay de um job com falha."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from app.core import orm
from app.pipeline.tce_job_queue import (
    SOURCE,
    listar_jobs_failed_tce_session,
    replay_job,
    replay_job_session,
)


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


def replay_failed(
    *,
    job_type: str | None,
    scope_prefix: str | None,
    limit: int,
    confirmar: bool,
) -> int:
    with orm.main_session() as session:
        candidatos = listar_jobs_failed_tce_session(
            session,
            job_type=job_type,
            scope_prefix=scope_prefix,
            limit=limit,
        )
        reabertos = [
            int(job["id"])
            for job in candidatos
            if confirmar and replay_job_session(session, job_id=int(job["id"]))
        ]
    print(json.dumps({
        "source": SOURCE,
        "dry_run": not confirmar,
        "candidate_count": len(candidatos),
        "replayed_count": len(reabertos),
        "replayed_job_ids": reabertos,
        "candidates": candidatos,
    }, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcomandos = parser.add_subparsers(dest="comando", required=True)
    subcomandos.add_parser("status", help="resume a fila por tipo e estado")
    replay = subcomandos.add_parser("replay", help="reativa um job FAILED")
    replay.add_argument("--job-id", type=int, required=True)
    replay_failed_parser = subcomandos.add_parser(
        "replay-failed",
        help="inspeciona ou reativa jobs FAILED do TCE em lote",
    )
    replay_failed_parser.add_argument("--job-type")
    replay_failed_parser.add_argument("--scope-prefix")
    replay_failed_parser.add_argument("--limit", type=int, default=100)
    replay_failed_parser.add_argument(
        "--confirm",
        action="store_true",
        help="aplica o replay; sem esta opção apenas lista candidatos",
    )
    argumentos = parser.parse_args()
    if argumentos.comando == "status":
        return status()
    if argumentos.comando == "replay-failed":
        if argumentos.confirm and not (argumentos.job_type or argumentos.scope_prefix):
            parser.error("--confirm exige --job-type ou --scope-prefix para limitar o replay.")
        return replay_failed(
            job_type=argumentos.job_type,
            scope_prefix=argumentos.scope_prefix,
            limit=argumentos.limit,
            confirmar=argumentos.confirm,
        )
    reagendado = replay_job(job_id=argumentos.job_id)
    print(json.dumps({"job_id": argumentos.job_id, "reagendado": reagendado}))
    return 0 if reagendado else 2


if __name__ == "__main__":
    raise SystemExit(main())
