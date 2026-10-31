"""Comandos operacionais do pipeline PNCP.

Exemplos no container:
  python scripts/pncp_operacao.py status
  python scripts/pncp_operacao.py coletar-publicacao --modalidade 6 --modalidade 8
  python scripts/pncp_operacao.py coletar-propostas-abertas
  python scripts/pncp_operacao.py normalizar --limite 100
  python scripts/pncp_operacao.py replay --job-id 123
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core import orm
from app.pncp.atualizacao import agendar_atualizacao, coletar_atualizacao
from app.pncp.ingestion import (
    ClientePncp,
    agendar_backfill_anual,
    agendar_propostas_abertas,
    agendar_publicacao,
    coletar_propostas_abertas,
    coletar_publicacao,
)
from app.pncp.jobs import WorkerPncp, agendar_job, replay_jobs
from app.pncp.normalization import normalizar_job
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session
from app.pncp.enrichment import ClienteEnriquecimentoPncp, agendar_enriquecimento_session, enriquecer_job
from app.core.models import PncpOportunidade


def _worker_coleta(tamanho_pagina: int, job_type: str = "incremental") -> WorkerPncp:
    cliente = ClientePncp()

    def executar(job: Any) -> None:
        if job.scope_key.startswith(("publicacao:", "backfill:")):
            coletar_publicacao(job, cliente, tamanho_pagina=tamanho_pagina)
        elif job.scope_key.startswith("atualizacao:"):
            coletar_atualizacao(job, cliente, tamanho_pagina=tamanho_pagina)
        else:
            raise ValueError(f"fluxo incremental desconhecido: {job.scope_key}")

    return WorkerPncp("pncp-operacao-coleta", executar, job_type=job_type)


def coletar(args: argparse.Namespace) -> int:
    if args.ano is not None and args.fluxo != "publicacao":
        raise ValueError("Backfill anual disponível apenas para publicações.")
    fim = datetime.now(timezone.utc)
    inicio = fim - timedelta(days=args.dias)
    modalidades = args.modalidade or [6]
    if args.fluxo == "publicacao":
        if args.ano is not None:
            if args.fluxo != "publicacao":
                raise ValueError("backfill anual está disponível apenas para publicacao")
            jobs = agendar_backfill_anual(ano=args.ano, modalidades=modalidades, tamanho_janela_dias=args.janela_dias, tamanho_pagina=args.tamanho_pagina)
        else:
            jobs = agendar_publicacao(inicio=inicio, fim=fim, modalidades=modalidades, tamanho_pagina=args.tamanho_pagina, prioridade=100)
    else:
        jobs = agendar_atualizacao(inicio=inicio, fim=fim, modalidades=modalidades)
    ids = [agendar_job(job)[0] for job in jobs]
    worker = _worker_coleta(args.tamanho_pagina, "backfill" if args.ano is not None else "incremental")
    executados = sum(worker.executar_uma_vez() for _ in jobs)
    with orm.main_session() as session:
        estados = session.execute(
            text("SELECT status, count(*) AS total FROM ingestion_jobs WHERE id = ANY(:ids) GROUP BY status"),
            {"ids": ids},
        ).mappings().all() if ids else []
    contagens = {linha["status"]: int(linha["total"]) for linha in estados}
    concluidos = contagens.get("SUCCEEDED", 0)
    print({"fluxo": args.fluxo, "jobs": ids, "tentativas": executados, "estados": contagens, "modalidades": modalidades})
    return 0 if not ids or concluidos == len(ids) else 2


def coletar_propostas_em_aberto(args: argparse.Namespace) -> int:
    jobs = agendar_propostas_abertas(
        data_final=datetime.now(timezone.utc),
        modalidades=args.modalidade,
        tamanho_pagina=args.tamanho_pagina,
    )
    ids = [agendar_job(job)[0] for job in jobs]
    worker = _worker_coleta(args.tamanho_pagina)
    executados = sum(worker.executar_uma_vez() for _ in jobs)
    print({"fluxo": "propostas-abertas", "jobs": ids, "executados": executados, "modalidades": args.modalidade or "todas"})
    return 0 if executados == len(jobs) else 2


def normalizar(args: argparse.Namespace) -> int:
    worker = WorkerPncp("pncp-operacao-normalizacao", normalizar_job, job_type="normalize")
    executados = sum(worker.executar_uma_vez() for _ in range(args.limite))
    print({"executados": executados, "limite": args.limite})
    return 0


def coletar_amostra(args: argparse.Namespace) -> int:
    """Persiste no maximo N registros da primeira pagina, sem avancar cursor."""
    cliente = ClientePncp()
    pagina = cliente.buscar_publicacoes(inicio=args.data, fim=args.data, modalidade=args.modalidade,
        pagina=1, tamanho_pagina=max(args.limite, 10))
    external_ids = []
    for payload in pagina.itens[:args.limite]:
        external_id = payload.get("numeroControlePNCP")
        if not isinstance(external_id, str) or not external_id.strip():
            continue
        with orm.main_session() as session:
            persistido = salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
                entity_type="CONTRATACAO", external_id=external_id, payload=payload,
                collected_at=datetime.now(timezone.utc), run_id=f"amostra:{args.data.isoformat()}:{args.modalidade}"))
        external_ids.append({"external_id": external_id, "raw_id": persistido.raw_record_id, "job_id": persistido.job_id})
    print({"data": args.data.isoformat(), "modalidade": args.modalidade, "limite": args.limite,
        "persistidos": len(external_ids), "total_paginas_fonte": pagina.total_paginas, "cursor_avancado": False, "editais": external_ids})
    return 0 if external_ids else 2


def agendar_enriquecimento(args: argparse.Namespace) -> int:
    agendados = {}
    with orm.main_session() as session:
        for external_id in args.id:
            oportunidade = session.get(PncpOportunidade, {"source": "PNCP", "external_id": external_id})
            if oportunidade is None:
                agendados[external_id] = "NAO_ENCONTRADO"
                continue
            agendados[external_id] = agendar_enriquecimento_session(session,
                oportunidade=oportunidade, motivo="validacao_local")
    print({"editais": agendados})
    return 0 if all(valor != "NAO_ENCONTRADO" for valor in agendados.values()) else 2


def executar_enriquecimento(args: argparse.Namespace) -> int:
    cliente = ClienteEnriquecimentoPncp()
    worker = WorkerPncp("pncp-operacao-enriquecimento",
        lambda job: enriquecer_job(job, cliente), job_type="enrich")
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
        comando.add_argument("--tamanho-pagina", type=int, default=50)
        comando.add_argument("--ano", type=int, help="Agenda backfill de publicações do ano informado")
        comando.add_argument("--janela-dias", type=int, default=7)
    propostas = comandos.add_parser("coletar-propostas-abertas")
    propostas.add_argument("--modalidade", type=int, action="append", default=[])
    propostas.add_argument("--tamanho-pagina", type=int, default=50)
    propostas.set_defaults(func=coletar_propostas_em_aberto)
    normalizador = comandos.add_parser("normalizar")
    normalizador.add_argument("--limite", type=int, default=100)
    normalizador.set_defaults(func=normalizar)
    amostra = comandos.add_parser("coletar-amostra", help="Importa poucos editais sem avançar cursor")
    amostra.add_argument("--data", type=date.fromisoformat, required=True)
    amostra.add_argument("--modalidade", type=int, default=6)
    amostra.add_argument("--limite", type=int, choices=range(1, 51), default=3)
    amostra.set_defaults(func=coletar_amostra)
    enriquecer = comandos.add_parser("agendar-enriquecimento")
    enriquecer.add_argument("--id", action="append", required=True)
    enriquecer.set_defaults(func=agendar_enriquecimento)
    enriquecer_run = comandos.add_parser("enriquecer")
    enriquecer_run.add_argument("--limite", type=int, default=10)
    enriquecer_run.set_defaults(func=executar_enriquecimento)
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
