#!/usr/bin/env python3
"""Ingere empenhos, anulacoes e pagamentos de todos os municipios do Ceara."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core import database, orm
from app.core.config import UF_PADRAO
from app.core.models import IbgeMunicipio, TceMunicipio
from app.pipeline.tce_pagamentos import executar_ingestao_competencia_pagamentos_tce
from scripts.ingestir_tce_despesas import (
    _listar_todos_municipios,
    main as ingerir_despesas,
)


FUSO_HORARIO_TCE = ZoneInfo("America/Fortaleza")


def _ano_valido(valor: str) -> str:
    if len(valor) != 4 or not valor.isdigit():
        raise argparse.ArgumentTypeError("Informe o exercicio no formato YYYY.")
    if int(valor) < 1900 or int(valor) > datetime.now(FUSO_HORARIO_TCE).year:
        raise argparse.ArgumentTypeError("O exercicio deve estar entre 1900 e o ano atual.")
    return valor


def _competencias_pagamentos(ano_orcamento: int, hoje: date) -> list[str]:
    inicio = ano_orcamento * 12
    fim = hoje.year * 12 + hoje.month - 1
    if inicio > fim:
        raise ValueError("O exercicio orcamentario nao pode ser posterior ao mes atual.")
    return [f"{indice // 12:04d}-{indice % 12 + 1:02d}" for indice in range(inicio, fim + 1)]


def _listar_codigos_municipios_ce() -> list[str]:
    database.init_db()
    with orm.main_session() as session:
        codigos = session.execute(
            select(TceMunicipio.codigo_municipio_tce)
            .join(
                IbgeMunicipio,
                IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
            )
            .where(IbgeMunicipio.uf == UF_PADRAO.upper())
            .order_by(TceMunicipio.codigo_municipio_tce)
        ).scalars().all()
    if len(codigos) != 184 or len(set(codigos)) != 184:
        raise RuntimeError(
            f"Catalogo TCE/IBGE incompleto para {UF_PADRAO}: "
            f"{len(codigos)} municipios unicos; esperado=184."
        )
    return [str(codigo) for codigo in codigos]


def _ingerir_pagamento(
    codigo_municipio: str,
    exercicio: int,
    competencia: str,
) -> dict[str, object]:
    try:
        resultado = executar_ingestao_competencia_pagamentos_tce(
            competencia,
            exercicio_orcamento=exercicio,
            codigo_municipio_tce=codigo_municipio,
        )
        return {
            "codigo_municipio_tce": codigo_municipio,
            **asdict(resultado),
        }
    except Exception as error:
        return {
            "codigo_municipio_tce": codigo_municipio,
            "exercicio_orcamento": exercicio * 100,
            "competencia": competencia,
            "status": "FALHA",
            "erro": f"{type(error).__name__}: {error}",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", required=True, type=_ano_valido, help="Exercicio orcamentario YYYY.")
    parser.add_argument(
        "--workers", type=int, choices=(1, 2), default=2,
        help="Coletas de pagamentos paralelas (padrao: 2).",
    )
    parser.add_argument(
        "--somente-pagamentos", "--apenas-pagamentos",
        dest="somente_pagamentos", action="store_true",
        help="Pula empenhos/anulacoes e ingere apenas pagamentos.",
    )
    args = parser.parse_args(argv)
    exercicio = int(args.ano)
    hoje = datetime.now(FUSO_HORARIO_TCE).date()
    competencias = _competencias_pagamentos(exercicio, hoje)

    status_despesas = 0
    if args.somente_pagamentos:
        print(json.dumps({
            "etapa": "EMPENHOS_E_ANULACOES",
            "status": "IGNORADA",
            "motivo": "somente_pagamentos",
        }), flush=True)
    else:
        print(json.dumps({
            "etapa": "EMPENHOS_E_ANULACOES",
            "ano": args.ano,
            "uf": UF_PADRAO,
            "status": "INICIADA",
        }), flush=True)
        status_despesas = ingerir_despesas(["--ano", args.ano, "--todos-municipios"])

    try:
        municipios = _listar_codigos_municipios_ce()
    except RuntimeError as error:
        if not args.somente_pagamentos or "Catalogo TCE/IBGE incompleto" not in str(error):
            print(json.dumps({
                "etapa": "PAGAMENTOS",
                "status": "FALHA",
                "erro": f"{type(error).__name__}: {error}",
            }, ensure_ascii=False), flush=True)
            return 1
        try:
            database.salvar_municipios_tce(_listar_todos_municipios(), UF_PADRAO)
            municipios = _listar_codigos_municipios_ce()
        except Exception as catalog_error:
            print(json.dumps({
                "etapa": "CATALOGO_MUNICIPIOS",
                "status": "FALHA",
                "erro": f"{type(catalog_error).__name__}: {catalog_error}",
            }, ensure_ascii=False), flush=True)
            return 1
    except Exception as error:
        print(json.dumps({
            "etapa": "PAGAMENTOS",
            "status": "FALHA",
            "erro": f"{type(error).__name__}: {error}",
        }, ensure_ascii=False), flush=True)
        return 1

    tarefas = [
        (municipio, competencia)
        for municipio in municipios
        for competencia in competencias
    ]
    concluidas = 0
    publicadas = 0
    falhas = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futuros = {
            executor.submit(_ingerir_pagamento, municipio, exercicio, competencia): (
                municipio, competencia,
            )
            for municipio, competencia in tarefas
        }
        for futuro in as_completed(futuros):
            municipio, competencia = futuros[futuro]
            try:
                resultado = futuro.result()
            except Exception as error:
                resultado = {
                    "codigo_municipio_tce": municipio,
                    "competencia": competencia,
                    "status": "FALHA",
                    "erro": f"{type(error).__name__}: {error}",
                }
            concluidas += 1
            publicado = resultado.get("status") == "PUBLICADO"
            publicadas += int(publicado)
            falhas += int(not publicado)
            print(json.dumps({
                "etapa": "PAGAMENTOS",
                "progresso": concluidas,
                "total": len(tarefas),
                **resultado,
            }, ensure_ascii=False, default=str), flush=True)

    print(json.dumps({
        "status": "CONCLUIDO" if not falhas and status_despesas == 0 else "CONCLUIDO_COM_FALHAS",
        "ano_orcamento": exercicio,
        "uf": UF_PADRAO,
        "municipios": len(municipios),
        "competencias_pagamentos": competencias,
        "lotes_pagamentos": len(tarefas),
        "pagamentos_publicados": publicadas,
        "falhas_pagamentos": falhas,
        "status_ingestao_empenhos_anulacoes": status_despesas,
    }, ensure_ascii=False), flush=True)
    return 0 if not falhas and status_despesas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
