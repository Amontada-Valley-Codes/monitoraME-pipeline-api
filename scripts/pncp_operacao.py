"""Comandos operacionais do pipeline PNCP.

Exemplos no container:
  python scripts/pncp_operacao.py status
  python scripts/pncp_operacao.py coletar-publicacao --modalidade 6 --modalidade 8
  python scripts/pncp_operacao.py normalizar --limite 100
  python scripts/pncp_operacao.py replay --job-id 123
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core import orm
from app.pncp.atualizacao import agendar_atualizacao, coletar_atualizacao
from app.pncp.ingestion import ClientePncp, agendar_publicacao, coletar_publicacao
from app.pncp.jobs import WorkerPncp, agendar_job, replay_jobs
from app.pncp.normalization import normalizar_job


def _worker_coleta(tamanho_pagina: int) -> WorkerPncp:
    cliente = ClientePncp()

    def executar(job: Any) -> None:
        if job.scope_key.startswith("publicacao:"):
            coletar_publicacao(job, cliente, tamanho_pagina=tamanho_pagina)
        elif job.scope_key.startswith("atualizacao:"):
            coletar_atualizacao(job, cliente, tamanho_pagina=tamanho_pagina)
        else:
            raise ValueError(f"fluxo incremental desconhecido: {job.scope_key}")

    return WorkerPncp("pncp-operacao-coleta", executar, job_type="incremental")


def coletar(args: argparse.Namespace) -> int:
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(days=args.dias)
    modalidades = args.modalidade or [6]
    if args.fluxo == "publicacao":
        jobs = agendar_publicacao(inicio=inicio, fim=fim, modalidades=modalidades, tamanho_pagina=args.tamanho_pagina, prioridade=100)
    else:
        jobs = agendar_atualizacao(inicio=inicio, fim=fim, modalidades=modalidades)
    ids = [agendar_job(job)[0] for job in jobs]
    worker = _worker_coleta(args.tamanho_pagina)
    executados = sum(worker.executar_uma_vez() for _ in jobs)
    print({"fluxo": args.fluxo, "jobs": ids, "executados": executados, "modalidades": modalidades})
    return 0 if executados == len(jobs) else 2


def normalizar(args: argparse.Namespace) -> int:
    worker = WorkerPncp("pncp-operacao-normalizacao", normalizar_job, job_type="normalize")
    executados = sum(worker.executar_uma_vez() for _ in range(args.limite))
    print({"executados": executados, "limite": args.limite})
    return 0


def status(_: argparse.Namespace) -> int:
    with orm.main_session() as session:
        jobs = session.execute(text("SELECT job_type, status, count(*) AS total FROM ingestion_jobs GROUP BY job_type, status ORDER BY job_type, status")).mappings().all()
        totais = session.execute(text("SELECT (SELECT count(*) FROM raw_pncp_records) AS raw, (SELECT count(*) FROM pncp_oportunidades) AS oportunidades")).mappings().one()
    print({"jobs": [dict(item) for item in jobs], "totais": dict(totais)})
    return 0


def replay(args: argparse.Namespace) -> int:
    ids = replay_jobs(job_id=args.job_id, flow=args.flow)
    print({"reagendados": ids})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)

    coleta = comandos.add_parser("coletar-publicacao")
    coleta.set_defaults(fluxo="publicacao", func=coletar)
    atualizacao = comandos.add_parser("coletar-atualizacao")
    atualizacao.set_defaults(fluxo="atualizacao", func=coletar)
    for comando in (coleta, atualizacao):
        comando.add_argument("--dias", type=int, default=1)
        comando.add_argument("--modalidade", type=int, action="append", default=[])
        comando.add_argument("--tamanho-pagina", type=int, default=100)
    normalizador = comandos.add_parser("normalizar")
    normalizador.add_argument("--limite", type=int, default=100)
    normalizador.set_defaults(func=normalizar)
    status_parser = comandos.add_parser("status")
    status_parser.set_defaults(func=status)
    replay_parser = comandos.add_parser("replay")
    replay_parser.add_argument("--job-id", type=int)
    replay_parser.add_argument("--flow")
    replay_parser.set_defaults(func=replay)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
