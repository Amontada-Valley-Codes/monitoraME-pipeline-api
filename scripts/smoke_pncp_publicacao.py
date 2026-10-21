"""Executa uma coleta PNCP de publicacao usando a fila PostgreSQL.

Uso no container:
python scripts/smoke_pncp_publicacao.py --dias 7 --modalidade 6 --tamanho-pagina 10
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from app.pncp.ingestion import ClientePncp, agendar_publicacao, coletar_publicacao
from app.pncp.jobs import WorkerPncp, agendar_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dias", type=int, default=7)
    parser.add_argument("--modalidade", type=int, action="append", default=[])
    parser.add_argument("--tamanho-pagina", type=int, default=10)
    parser.add_argument("--worker-id", default="smoke-pncp-publicacao")
    args = parser.parse_args()
    if args.dias < 1:
        parser.error("--dias deve ser maior que zero")
    modalidades = args.modalidade or [6]
    agora = datetime.now(timezone.utc)
    inicio = agora - timedelta(days=args.dias)
    jobs = agendar_publicacao(
        inicio=inicio,
        fim=agora,
        modalidades=modalidades,
        tamanho_pagina=args.tamanho_pagina,
        prioridade=100,
    )
    agendados = [agendar_job(job) for job in jobs]
    print({"janela_inicio": inicio.isoformat(), "janela_fim": agora.isoformat(), "agendados": agendados})
    cliente = ClientePncp()
    worker = WorkerPncp(
        args.worker_id,
        lambda job: print({"job_id": job.id, "registros": coletar_publicacao(job, cliente, tamanho_pagina=args.tamanho_pagina)}),
    )
    executados = 0
    for _ in jobs:
        if worker.executar_uma_vez():
            executados += 1
    print({"executados": executados, "modalidades": modalidades})
    return 0 if executados == len(jobs) else 2


if __name__ == "__main__":
    raise SystemExit(main())
