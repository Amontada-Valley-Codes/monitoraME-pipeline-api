#!/usr/bin/env python3
"""Coleta, valida e publica empenhos e anulações de uma competência do TCE-CE."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime
import json
import sys

from app.core import database
from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO, UF_PADRAO
from app.pipeline.ingestion import ibge, tce
from app.pipeline.tce_despesas import executar_ingestao_competencia_tce


def _competencia_valida(valor: str) -> str:
    try:
        datetime.strptime(valor, "%Y-%m")
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use uma competência válida no formato YYYY-MM.") from error
    if len(valor) != 7:
        raise argparse.ArgumentTypeError("Use uma competência válida no formato YYYY-MM.")
    return valor


def _ano_valido(valor: str) -> str:
    if len(valor) != 4 or not valor.isdigit() or int(valor) < 1:
        raise argparse.ArgumentTypeError("Informe o ano no formato YYYY.")
    if int(valor) > date.today().year:
        raise argparse.ArgumentTypeError("O ano não pode estar no futuro.")
    return valor


def _competencias_do_ano(ano: str) -> list[str]:
    ultimo_mes = date.today().month if int(ano) == date.today().year else 12
    return [f"{ano}-{mes:02d}" for mes in range(1, ultimo_mes + 1)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    periodo = parser.add_mutually_exclusive_group(required=True)
    periodo.add_argument(
        "--competencia",
        type=_competencia_valida,
        help="Competência dos documentos, no formato YYYY-MM.",
    )
    periodo.add_argument(
        "--ano",
        type=_ano_valido,
        help="Coleta todas as competências disponíveis do ano, no formato YYYY.",
    )
    parser.add_argument(
        "--codigo-municipio",
        default=CODIGO_MUNICIPIO_TCE_PADRAO,
        help=f"Código do município no TCE-CE (padrão: {CODIGO_MUNICIPIO_TCE_PADRAO}).",
    )
    parser.add_argument(
        "--todos-municipios",
        action="store_true",
        help=f"Executa a competência para todos os municípios de {UF_PADRAO} validados pelo IBGE.",
    )
    args = parser.parse_args(argv)
    competencias = (
        [args.competencia]
        if args.competencia
        else _competencias_do_ano(args.ano)
    )

    if args.todos_municipios:
        if args.codigo_municipio != CODIGO_MUNICIPIO_TCE_PADRAO:
            parser.error("--codigo-municipio não pode ser combinado com --todos-municipios.")
        try:
            municipios = _listar_todos_municipios()
        except Exception as error:
            print(
                json.dumps(
                    {
                        "status": "FALHA",
                        "periodo": args.ano or args.competencia,
                        "uf": UF_PADRAO,
                        "erro": f"{type(error).__name__}: {error}",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            return 1

        codigos = [str(municipio["codigo_municipio"]) for municipio in municipios]
        database.salvar_municipios_tce(municipios, UF_PADRAO)
        codigos_ok = 0
        for competencia in competencias:
            codigo_saida = _ingerir_todos_municipios(competencia, municipios)
            codigos_ok += codigo_saida == 0
        print(
            json.dumps(
                {
                    "status": "CONCLUIDO" if codigos_ok == len(competencias) else "CONCLUIDO_COM_FALHAS",
                    "ano": args.ano,
                    "competencias": competencias,
                    "uf": UF_PADRAO,
                    "municipios": len(codigos),
                    "competencias_publicadas": codigos_ok,
                    "competencias_com_falhas": len(competencias) - codigos_ok,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0 if codigos_ok == len(competencias) else 2

    resultados = []
    for competencia in competencias:
        try:
            resultado = executar_ingestao_competencia_tce(
                competencia,
                codigo_municipio_tce=args.codigo_municipio,
            )
            resumo = {
                "competencia": competencia,
                "codigo_municipio_tce": args.codigo_municipio,
                **asdict(resultado),
            }
            resultados.append(resultado.status == "PUBLICADO")
        except Exception as error:
            resumo = {
                "competencia": competencia,
                "codigo_municipio_tce": args.codigo_municipio,
                "status": "FALHA",
                "erro": f"{type(error).__name__}: {error}",
            }
            resultados.append(False)
        print(json.dumps(resumo, ensure_ascii=False, default=str), flush=True)

    if args.ano:
        falhas = len(resultados) - sum(resultados)
        print(
            json.dumps(
                {
                    "status": "CONCLUIDO" if falhas == 0 else "CONCLUIDO_COM_FALHAS",
                    "ano": args.ano,
                    "competencias": len(resultados),
                    "publicadas": sum(resultados),
                    "falhas_ou_rejeitadas": falhas,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if all(resultados) else 2


def _listar_todos_municipios() -> list[dict[str, object]]:
    municipios_ibge = ibge.sincronizar_municipios(UF_PADRAO)
    codigos_ibge = {str(municipio["id"]) for municipio in municipios_ibge}
    municipios_tce = tce.buscar_municipios()
    municipios = [
        municipio
        for municipio in municipios_tce
        if str(municipio.get("codigo_municipio_ibge")) in codigos_ibge
    ]
    codigos_tce = [str(municipio["codigo_municipio"]) for municipio in municipios]
    if len(codigos_ibge) != 184 or len(municipios) != 184 or len(set(codigos_tce)) != 184:
        raise RuntimeError(
            f"Catálogo de municípios de {UF_PADRAO} inválido: "
            f"IBGE={len(codigos_ibge)}, TCE={len(municipios)}, "
            f"códigos únicos={len(set(codigos_tce))}; esperado=184."
        )
    return municipios


def _ingerir_todos_municipios(
    competencia: str,
    municipios: list[dict[str, object]],
) -> int:

    resultados: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = {
            executor.submit(
                executar_ingestao_competencia_tce,
                competencia,
                codigo_municipio_tce=str(municipio["codigo_municipio"]),
            ): municipio
            for municipio in municipios
        }
        for futuro in as_completed(futuros):
            municipio = futuros[futuro]
            item: dict[str, object] = {
                "codigo_municipio_tce": str(municipio["codigo_municipio"]),
                "nome_municipio": municipio["nome_municipio"],
            }
            try:
                item.update(asdict(futuro.result()))
            except Exception as error:
                item.update(
                    status="FALHA",
                    erro=f"{type(error).__name__}: {error}",
                )
            resultados.append(item)
            print(
                json.dumps(
                    {
                        "progresso": len(resultados),
                        "total": len(municipios),
                        **item,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )

    publicados = sum(item.get("status") == "PUBLICADO" for item in resultados)
    falhas = len(resultados) - publicados
    print(
        json.dumps(
            {
                "status": "CONCLUIDO" if falhas == 0 else "CONCLUIDO_COM_FALHAS",
                "competencia": competencia,
                "uf": UF_PADRAO,
                "municipios": len(resultados),
                "publicados": publicados,
                "falhas_ou_rejeitados": falhas,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if falhas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
