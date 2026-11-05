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
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from app.core import orm
from app.pncp.atualizacao import agendar_atualizacao, coletar_atualizacao
from app.pncp.ingestion import (
    ClientePncp,
    MODALIDADES_PRIORITARIAS,
    UFS_NORDESTE,
    agendar_backfill_anual,
    agendar_propostas_abertas,
    agendar_publicacao,
    coletar_publicacao,
)
from app.pncp.jobs import WorkerPncp, agendar_job, replay_jobs
from app.pncp.normalization import agendar_reprocessamento_regra_session, normalizar_job
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session
from app.pncp.enrichment import ClienteEnriquecimentoPncp, agendar_enriquecimento_session, enriquecer_job
from app.core.models import PncpOportunidade
from app.pncp import taxonomy
from app.pncp.cnae_ibge import buscar_catalogo_session


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
    modalidades = args.modalidade or list(MODALIDADES_PRIORITARIAS)
    ufs = args.uf or list(UFS_NORDESTE)
    if args.fluxo == "publicacao":
        if args.ano is not None:
            if args.fluxo != "publicacao":
                raise ValueError("backfill anual está disponível apenas para publicacao")
            jobs = agendar_backfill_anual(ano=args.ano, modalidades=modalidades, ufs=ufs, tamanho_janela_dias=args.janela_dias, tamanho_pagina=args.tamanho_pagina)
        else:
            jobs = agendar_publicacao(inicio=inicio, fim=fim, modalidades=modalidades, ufs=ufs, tamanho_pagina=args.tamanho_pagina, prioridade=100)
    else:
        jobs = agendar_atualizacao(inicio=inicio, fim=fim, modalidades=modalidades, ufs=ufs)
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
    print({"fluxo": args.fluxo, "jobs": ids, "tentativas": executados, "estados": contagens, "ufs": ufs, "modalidades": modalidades})
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


def reprocessar_normalizacao(args: argparse.Namespace) -> int:
    with orm.main_session() as session:
        resultado = agendar_reprocessamento_regra_session(session, limite=args.limite)
    print(resultado)
    return 0


def coletar_amostra(args: argparse.Namespace) -> int:
    """Persiste no maximo N registros paginados, sem avancar cursor."""
    inicio = args.data_inicial or args.data
    fim = args.data_final or args.data
    if inicio is None or fim is None or fim < inicio:
        raise ValueError("informe --data ou um intervalo valido com --data-inicial e --data-final")
    cliente = ClientePncp()
    external_ids: list[str] = []
    vistos: set[str] = set()
    paginas_lidas = 0
    for numero_pagina in range(1, args.max_paginas + 1):
        pagina = cliente.buscar_publicacoes(
            inicio=inicio, fim=fim, modalidade=args.modalidade,
            pagina=numero_pagina, tamanho_pagina=args.tamanho_pagina,
        )
        paginas_lidas += 1
        if not pagina.itens and pagina.total_paginas is not None and numero_pagina < pagina.total_paginas:
            raise RuntimeError("pagina vazia antes do fim informado pelo PNCP; a amostra pode ser repetida")
        for payload in pagina.itens:
            external_id = payload.get("numeroControlePNCP")
            if not isinstance(external_id, str) or not external_id.strip():
                continue
            if external_id in vistos:
                continue
            vistos.add(external_id)
            with orm.main_session() as session:
                salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
                    entity_type="CONTRATACAO", external_id=external_id, payload=payload,
                    collected_at=datetime.now(timezone.utc),
                    run_id=f"amostra:{inicio.isoformat()}:{fim.isoformat()}:{args.modalidade}"))
            external_ids.append(external_id)
            if len(external_ids) >= args.limite:
                break
        if len(external_ids) >= args.limite or not pagina.itens or (
            pagina.total_paginas is None and len(pagina.itens) < args.tamanho_pagina
        ) or (
            pagina.total_paginas is not None and numero_pagina >= pagina.total_paginas
        ):
            break
    print({"data_inicial": inicio.isoformat(), "data_final": fim.isoformat(), "modalidade": args.modalidade,
        "limite": args.limite, "persistidos": len(external_ids), "paginas_lidas": paginas_lidas,
        "cursor_avancado": False})
    return 0 if external_ids else 2


def coletar_amostra_abertas(args: argparse.Namespace) -> int:
    """Busca manual e limitada pelo endpoint PNCP de propostas ainda abertas."""
    cliente = ClientePncp()
    external_ids: list[str] = []
    vistos: set[str] = set()
    paginas_lidas = 0
    data_final = datetime.now(timezone.utc).date()
    for pagina_numero in range(1, args.max_paginas + 1):
        pagina = cliente.buscar_propostas_abertas(
            data_final=data_final,
            modalidade=args.modalidade,
            pagina=pagina_numero,
            tamanho_pagina=args.tamanho_pagina,
        )
        paginas_lidas += 1
        for payload in pagina.itens:
            external_id = payload.get("numeroControlePNCP")
            if not isinstance(external_id, str) or not external_id.strip() or external_id in vistos:
                continue
            vistos.add(external_id)
            with orm.main_session() as session:
                salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
                    entity_type="CONTRATACAO", external_id=external_id, payload=payload,
                    collected_at=datetime.now(timezone.utc),
                    run_id=f"amostra-abertas:{data_final.isoformat()}:{args.modalidade or 'todas'}",
                ))
            external_ids.append(external_id)
            if len(external_ids) >= args.limite:
                break
        if len(external_ids) >= args.limite or not pagina.itens or (
            pagina.total_paginas is not None and pagina_numero >= pagina.total_paginas
        ) or (
            pagina.total_paginas is None and len(pagina.itens) < args.tamanho_pagina
        ):
            break
    print({
        "fluxo": "propostas-abertas", "limite": args.limite,
        "persistidos": len(external_ids), "paginas_lidas": paginas_lidas,
        "data_final": data_final.isoformat(), "modalidade": args.modalidade or "todas",
        "cursor_avancado": False, "normalizacao_agendada": len(external_ids),
    })
    return 0 if external_ids else 2


def agendar_enriquecimento(args: argparse.Namespace) -> int:
    agendados = {}
    with orm.main_session() as session:
        external_ids = args.id
        if args.todos_normalizados:
            external_ids = list(session.scalars(
                select(PncpOportunidade.external_id)
                .order_by(PncpOportunidade.external_id)
                .limit(args.limite)
            ))
        for external_id in external_ids:
            oportunidade = session.get(PncpOportunidade, {"source": "PNCP", "external_id": external_id})
            if oportunidade is None:
                agendados[external_id] = "NAO_ENCONTRADO"
                continue
            agendados[external_id] = agendar_enriquecimento_session(session,
                oportunidade=oportunidade, motivo="validacao_local")
    resultados = [resultado for resultado in agendados.values() if isinstance(resultado, list)]
    jobs_agendados = [job for resultado in resultados for job in resultado]
    jobs_novos = sum(int(criado) for _, criado in jobs_agendados)
    nao_encontrados = sum(valor == "NAO_ENCONTRADO" for valor in agendados.values())
    print({
        "editais": len(agendados), "jobs_avaliados": len(jobs_agendados),
        "jobs_novos": jobs_novos, "nao_encontrados": nao_encontrados,
    })
    return 0 if nao_encontrados == 0 else 2


def executar_enriquecimento(args: argparse.Namespace) -> int:
    cliente = ClienteEnriquecimentoPncp()
    worker = WorkerPncp("pncp-operacao-enriquecimento",
        lambda job: enriquecer_job(job, cliente), job_type="enrich")
    executados = 0
    pausa_circuito = None
    for _ in range(args.limite):
        metricas = cliente.protecao.metricas()
        aberto_ate = metricas.get("open_until")
        if (
            metricas.get("circuit_state") == "OPEN"
            and aberto_ate is not None
            and aberto_ate > datetime.now(timezone.utc)
        ):
            pausa_circuito = aberto_ate.isoformat()
            break
        if not worker.executar_uma_vez():
            break
        executados += 1
    print({"executados": executados, "limite": args.limite, "circuito_aberto_ate": pausa_circuito})
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


def gerenciar_taxonomia(args: argparse.Namespace) -> int:
    if args.acao == "status":
        with orm.main_session() as session:
            resultado = taxonomy.obter_snapshot(session, args.versao)
    elif args.acao == "buscar":
        with orm.main_session() as session:
            resultado = buscar_catalogo_session(session, consulta=args.consulta, limite=args.limite)
    elif args.acao == "revisar":
        with orm.main_session() as session:
            origem = taxonomy.obter_versao_ativa(session)
            if origem is None:
                raise ValueError("Não existe versão ativa para copiar")
            taxonomy.criar_revisao(
                session, versao=args.versao, evidencia=args.evidencia,
                responsavel=args.responsavel,
            )
            session.flush()
            copiados = taxonomy.copiar_relacoes(
                session, versao_origem=origem, versao_destino=args.versao,
            )
        resultado = {"rascunho": args.versao, "copiada_de": origem, "relacoes_copiadas": copiados}
    elif args.acao == "mapear":
        with orm.main_session() as session:
            relacao = taxonomy.adicionar_relacao(
                session, versao=args.versao, cnae=args.cnae,
                categoria=args.categoria, tipo=args.tipo,
                confianca=args.confianca, responsavel=args.responsavel,
                evidencias=args.evidencia, condicao=args.condicao,
            )
            resultado = {
                "versao": relacao.versao, "cnae": relacao.cnae_codigo,
                "categoria": relacao.categoria_slug, "tipo": relacao.tipo,
                "confianca": relacao.confianca, "ativo": relacao.ativo,
            }
    elif args.acao == "atualizar":
        with orm.main_session() as session:
            relacao = taxonomy.atualizar_relacao(
                session, versao=args.versao, cnae=args.cnae,
                categoria=args.categoria, tipo=args.tipo,
                confianca=args.confianca, responsavel=args.responsavel,
                evidencias=args.evidencia, ativo=args.ativo,
                condicao=args.condicao,
            )
            resultado = {
                "versao": relacao.versao, "cnae": relacao.cnae_codigo,
                "categoria": relacao.categoria_slug, "tipo": relacao.tipo,
                "confianca": relacao.confianca, "ativo": relacao.ativo,
            }
    elif args.acao == "desativar":
        with orm.main_session() as session:
            relacao = taxonomy.desativar_relacao(
                session, versao=args.versao, cnae=args.cnae,
                categoria=args.categoria, responsavel=args.responsavel,
                evidencia=args.evidencia,
            )
            resultado = {"versao": relacao.versao, "cnae": relacao.cnae_codigo,
                         "categoria": relacao.categoria_slug, "ativo": relacao.ativo}
    else:
        with orm.main_session() as session:
            taxonomy.ativar_revisao(session, versao=args.versao, responsavel=args.responsavel)
        resultado = {"versao_ativa": args.versao}
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
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
        comando.add_argument("--uf", choices=UFS_NORDESTE, action="append", default=[])
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
    reprocessador = comandos.add_parser("reprocessar-normalizacao", help="Reprocessa RAW atual com regra semantica antiga")
    reprocessador.add_argument("--limite", type=int, default=2000)
    reprocessador.set_defaults(func=reprocessar_normalizacao)
    amostra = comandos.add_parser("coletar-amostra", help="Importa poucos editais sem avançar cursor")
    amostra.add_argument("--data", type=date.fromisoformat, help="Data única (atalho para inicio/fim)")
    amostra.add_argument("--data-inicial", type=date.fromisoformat)
    amostra.add_argument("--data-final", type=date.fromisoformat)
    amostra.add_argument("--modalidade", type=int, default=6)
    amostra.add_argument("--limite", type=int, choices=range(1, 2001), default=3)
    amostra.add_argument("--tamanho-pagina", type=int, choices=range(10, 51), default=50)
    amostra.add_argument("--max-paginas", type=int, choices=range(1, 201), default=200,
        help="Limite de protecao; cada pagina aceita no maximo 50 registros")
    amostra.set_defaults(func=coletar_amostra)
    amostra_abertas = comandos.add_parser(
        "coletar-amostra-abertas",
        help="Busca manualmente apenas contratações com recebimento de propostas aberto",
    )
    amostra_abertas.add_argument("--modalidade", type=int)
    amostra_abertas.add_argument("--limite", type=int, choices=range(1, 201), default=20)
    amostra_abertas.add_argument("--tamanho-pagina", type=int, choices=range(10, 51), default=20)
    amostra_abertas.add_argument("--max-paginas", type=int, choices=range(1, 21), default=10)
    amostra_abertas.set_defaults(func=coletar_amostra_abertas)
    enriquecer = comandos.add_parser("agendar-enriquecimento")
    alvo_enriquecimento = enriquecer.add_mutually_exclusive_group(required=True)
    alvo_enriquecimento.add_argument("--id", action="append")
    alvo_enriquecimento.add_argument("--todos-normalizados", action="store_true")
    enriquecer.add_argument("--limite", type=int, default=2000)
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
    taxonomia = comandos.add_parser("taxonomia", help="Busca CNAEs e revisa o de-para versionado")
    acoes_taxonomia = taxonomia.add_subparsers(dest="acao", required=True)
    tax_status = acoes_taxonomia.add_parser("status", help="Exibe snapshot e métricas da versão ativa ou informada")
    tax_status.add_argument("--versao")
    tax_status.set_defaults(func=gerenciar_taxonomia)
    tax_busca = acoes_taxonomia.add_parser("buscar", help="Busca código/descrição no catálogo CNAE sincronizado do IBGE")
    tax_busca.add_argument("--consulta", required=True)
    tax_busca.add_argument("--limite", type=int, default=20)
    tax_busca.set_defaults(func=gerenciar_taxonomia)
    tax_revisar = acoes_taxonomia.add_parser("revisar", help="Cria rascunho copiando a versão ativa")
    tax_revisar.add_argument("--versao", required=True)
    tax_revisar.add_argument("--responsavel", required=True)
    tax_revisar.add_argument("--evidencia", required=True)
    tax_revisar.set_defaults(func=gerenciar_taxonomia)
    tax_mapear = acoes_taxonomia.add_parser("mapear", help="Adiciona associação em um rascunho")
    tax_mapear.add_argument("--versao", required=True)
    tax_mapear.add_argument("--cnae", required=True)
    tax_mapear.add_argument("--categoria", required=True)
    tax_mapear.add_argument("--tipo", required=True, choices=("DIRETA", "RELACIONADA", "CONDICIONAL"))
    tax_mapear.add_argument("--confianca", required=True, type=float)
    tax_mapear.add_argument("--responsavel", required=True)
    tax_mapear.add_argument("--evidencia", required=True, action="append")
    tax_mapear.add_argument("--condicao")
    tax_mapear.set_defaults(func=gerenciar_taxonomia)
    tax_atualizar = acoes_taxonomia.add_parser("atualizar", help="Atualiza associação existente no rascunho")
    tax_atualizar.add_argument("--versao", required=True)
    tax_atualizar.add_argument("--cnae", required=True)
    tax_atualizar.add_argument("--categoria", required=True)
    tax_atualizar.add_argument("--tipo", required=True, choices=("DIRETA", "RELACIONADA", "CONDICIONAL"))
    tax_atualizar.add_argument("--confianca", required=True, type=float)
    tax_atualizar.add_argument("--responsavel", required=True)
    tax_atualizar.add_argument("--evidencia", required=True, action="append")
    estado_relacao = tax_atualizar.add_mutually_exclusive_group(required=True)
    estado_relacao.add_argument("--ativo", dest="ativo", action="store_true")
    estado_relacao.add_argument("--inativo", dest="ativo", action="store_false")
    tax_atualizar.add_argument("--condicao")
    tax_atualizar.set_defaults(func=gerenciar_taxonomia)
    tax_desativar = acoes_taxonomia.add_parser("desativar", help="Desativa uma associação no rascunho, preservando a trilha")
    tax_desativar.add_argument("--versao", required=True)
    tax_desativar.add_argument("--cnae", required=True)
    tax_desativar.add_argument("--categoria", required=True)
    tax_desativar.add_argument("--responsavel", required=True)
    tax_desativar.add_argument("--evidencia", required=True)
    tax_desativar.set_defaults(func=gerenciar_taxonomia)
    tax_ativar = acoes_taxonomia.add_parser("ativar", help="Ativa um rascunho e substitui o mapa ativo")
    tax_ativar.add_argument("--versao", required=True)
    tax_ativar.add_argument("--responsavel", required=True)
    tax_ativar.set_defaults(func=gerenciar_taxonomia)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
