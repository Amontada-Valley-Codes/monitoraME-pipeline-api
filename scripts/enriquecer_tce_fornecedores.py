#!/usr/bin/env python3
"""Enriquece fornecedores dos empenhos TCE-CE já publicados usando o cache OpenCNPJ."""

from __future__ import annotations

import argparse
from datetime import date
import json
import sys

from app.core.config import UF_PADRAO
from app.pipeline.ingestion.fornecedores import obter_fornecedores_com_cache
from app.pipeline.persistence.tce_despesas import listar_cnpjs_fornecedores_publicados
from app.pipeline.persistence.tce_enriquecimento import (
    listar_municipios_enriquecimento, municipios_concluidos, salvar_municipio_concluido,
)
from app.utils import normalizar_cnpj


def _ano_valido(valor: str) -> str:
    if len(valor) != 4 or not valor.isdigit() or int(valor) < 1:
        raise argparse.ArgumentTypeError("Informe o ano no formato YYYY.")
    if int(valor) > date.today().year:
        raise argparse.ArgumentTypeError("O ano não pode estar no futuro.")
    return valor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    escopo = parser.add_mutually_exclusive_group(required=True)
    escopo.add_argument("--ano", type=_ano_valido, help="Ano dos empenhos publicados (YYYY).")
    escopo.add_argument(
        "--todos-disponiveis",
        action="store_true",
        help="Usa empenhos de todos os anos atualmente publicados no banco.",
    )
    parser.add_argument(
        "--codigo-municipio",
        help="Limita a um município TCE; omitido, usa todos os municípios da UF configurada.",
    )
    parser.add_argument(
        "--retomar-municipios",
        action="store_true",
        help="Processa um municipio por vez e pula os concluidos no ano em execucoes anteriores.",
    )
    parser.add_argument(
        "--lote",
        type=int,
        default=100,
        choices=range(1, 1001),
        metavar="1..1000",
        help="CNPJs por lote de processamento (padrão: 100).",
    )
    parser.add_argument(
        "--intervalo-segundos",
        type=float,
        default=0.3,
        help="Intervalo entre consultas cadastrais em cache miss (padrão: 0.3).",
    )
    args = parser.parse_args(argv)
    if args.retomar_municipios and (args.ano is None or args.codigo_municipio is not None):
        parser.error("--retomar-municipios exige --ano e nao aceita --codigo-municipio.")
    if args.intervalo_segundos < 0:
        parser.error("--intervalo-segundos deve ser maior ou igual a zero.")

    ano = int(args.ano) if args.ano is not None else None
    data_inicial = date(ano, 1, 1) if ano is not None else None
    data_final = date(ano, 12, 31) if ano is not None else None
    if args.retomar_municipios:
        try:
            codigos = listar_municipios_enriquecimento(UF_PADRAO)
            if not codigos:
                raise ValueError("Nenhum municipio cadastrado para a UF configurada.")
            concluidos = municipios_concluidos(ano)
            falhas = 0
            pendencias = 0
            for codigo in codigos:
                if codigo in concluidos:
                    print(json.dumps({"municipio": codigo, "status": "PULADO"}), flush=True)
                    continue
                try:
                    cnpjs = sorted({cnpj for valor in listar_cnpjs_fornecedores_publicados(
                        data_inicial=data_inicial, data_final=data_final,
                        codigo_municipio_tce=codigo,
                    ) if (cnpj := normalizar_cnpj(valor))})
                    com_porte = 0
                    indisponiveis = 0
                    for inicio in range(0, len(cnpjs), args.lote):
                        dados = obter_fornecedores_com_cache(
                            cnpjs[inicio:inicio + args.lote],
                            throttle_segundos=args.intervalo_segundos,
                        )
                        if "porte_padronizado" in dados.columns:
                            com_porte += int(dados["porte_padronizado"].notna().sum())
                        if "opencnpj_status" in dados.columns:
                            indisponiveis += int(dados["opencnpj_status"].eq("indisponivel").sum())
                        if "cache_desatualizado" in dados.columns:
                            indisponiveis += int(dados["cache_desatualizado"].fillna(False).sum())
                    if indisponiveis:
                        pendencias += 1
                        status = "PENDENTE_FONTE_INDISPONIVEL"
                    else:
                        salvar_municipio_concluido(ano, codigo, len(cnpjs), com_porte)
                        status = "CONCLUIDO"
                    print(json.dumps({"municipio": codigo, "status": status,
                                      "total_cnpjs": len(cnpjs), "cnpjs_com_porte": com_porte,
                                      "cnpjs_indisponiveis": indisponiveis}), flush=True)
                except Exception as error:
                    falhas += 1
                    print(json.dumps({"municipio": codigo, "status": "FALHA",
                                      "erro": f"{type(error).__name__}: {error}"}),
                          file=sys.stderr, flush=True)
            print(json.dumps({"ano": ano, "municipios": len(codigos),
                              "ja_concluidos": len(concluidos), "falhas": falhas,
                              "pendencias": pendencias}), flush=True)
            return 1 if falhas else (2 if pendencias else 0)
        except Exception as error:
            print(json.dumps({"status": "FALHA", "erro": f"{type(error).__name__}: {error}"}),
                  file=sys.stderr, flush=True)
            return 1
    try:
        cnpjs = listar_cnpjs_fornecedores_publicados(
            data_inicial=data_inicial,
            data_final=data_final,
            codigo_municipio_tce=args.codigo_municipio,
            uf=UF_PADRAO if args.codigo_municipio is None else None,
        )
        cnpjs = sorted(
            {
                cnpj
                for valor in cnpjs
                if (cnpj := normalizar_cnpj(valor))
            }
        )
        total = len(cnpjs)
        com_porte = 0
        processados = 0
        for inicio in range(0, total, args.lote):
            lote = cnpjs[inicio : inicio + args.lote]
            dados = obter_fornecedores_com_cache(
                lote,
                throttle_segundos=args.intervalo_segundos,
            )
            if "porte_padronizado" in dados.columns:
                com_porte += int(dados["porte_padronizado"].notna().sum())
            processados += len(lote)
            print(
                json.dumps(
                    {
                        "progresso": processados,
                        "total_cnpjs": total,
                        "cnpjs_com_porte": com_porte,
                        "cnpjs_sem_porte_ate_agora": processados - com_porte,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FALHA",
                    "ano": args.ano,
                    "todos_disponiveis": args.todos_disponiveis,
                    "codigo_municipio_tce": args.codigo_municipio,
                    "erro": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1

    status = "CONCLUIDO" if com_porte == total else "CONCLUIDO_COM_AUSENCIAS"
    print(
        json.dumps(
            {
                "status": status,
                "ano": args.ano,
                "todos_disponiveis": args.todos_disponiveis,
                "uf": UF_PADRAO if args.codigo_municipio is None else None,
                "codigo_municipio_tce": args.codigo_municipio,
                "cnpjs_distintos_processados": processados,
                "cnpjs_com_porte_identificado": com_porte,
                "cnpjs_sem_porte": processados - com_porte,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if com_porte == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
