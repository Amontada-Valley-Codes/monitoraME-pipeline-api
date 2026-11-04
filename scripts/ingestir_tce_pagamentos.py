#!/usr/bin/env python3
"""Coleta e publica uma competência de notas_pagamentos do TCE-CE."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json

from app.core.config import CODIGO_MUNICIPIO_TCE_PADRAO
from app.pipeline.tce_pagamentos import executar_ingestao_competencia_pagamentos_tce


def _competencia(valor: str) -> str:
    try:
        if len(valor) != 7 or datetime.strptime(valor, "%Y-%m").strftime("%Y-%m") != valor:
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use uma competência válida no formato YYYY-MM.") from error
    return valor


def _exercicio(valor: str) -> int:
    if len(valor) != 4 or not valor.isdigit() or not 1900 <= int(valor) <= date.today().year:
        raise argparse.ArgumentTypeError("Informe o exercício orçamentário no formato YYYY.")
    return int(valor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    recorte = parser.add_mutually_exclusive_group(required=True)
    recorte.add_argument("--competencia", type=_competencia, help="Uma competência documental YYYY-MM.")
    recorte.add_argument(
        "--competencia-inicial",
        type=_competencia,
        help="Primeira competência documental inclusiva; use com --competencia-final.",
    )
    parser.add_argument("--competencia-final", type=_competencia, help="Última competência inclusiva do intervalo.")
    parser.add_argument("--exercicio-orcamento", required=True, type=_exercicio, help="Ano do orçamento, YYYY.")
    parser.add_argument(
        "--codigo-municipio",
        default=CODIGO_MUNICIPIO_TCE_PADRAO,
        help=f"Código do município no TCE-CE (padrão: {CODIGO_MUNICIPIO_TCE_PADRAO}).",
    )
    args = parser.parse_args(argv)
    if args.competencia:
        if args.competencia_final:
            parser.error("--competencia-final só pode ser usado com --competencia-inicial.")
        competencias = [args.competencia]
    else:
        if not args.competencia_final:
            parser.error("--competencia-inicial exige --competencia-final.")
        competencias = _intervalo_competencias(args.competencia_inicial, args.competencia_final)

    resultados = []
    for competencia in competencias:
        try:
            resultado = executar_ingestao_competencia_pagamentos_tce(
                competencia,
                exercicio_orcamento=args.exercicio_orcamento,
                codigo_municipio_tce=args.codigo_municipio,
            )
            resultados.append(asdict(resultado))
        except Exception as error:
            resultados.append({
                "status": "FALHA",
                "competencia": competencia,
                "exercicio_orcamento": args.exercicio_orcamento * 100,
                "codigo_municipio_tce": args.codigo_municipio,
                "erro": f"{type(error).__name__}: {error}",
            })
        print(json.dumps(resultados[-1], ensure_ascii=False, default=str), flush=True)
    falhas = sum(item.get("status") != "PUBLICADO" for item in resultados)
    print(json.dumps({
        "status": "CONCLUIDO" if falhas == 0 else "CONCLUIDO_COM_FALHAS",
        "competencias": len(competencias),
        "publicadas": len(competencias) - falhas,
        "falhas": falhas,
        "codigo_municipio_tce": args.codigo_municipio,
        "exercicio_orcamento": args.exercicio_orcamento * 100,
    }, ensure_ascii=False), flush=True)
    return 0 if falhas == 0 else 2


def _intervalo_competencias(inicio: str, fim: str) -> list[str]:
    primeiro = date.fromisoformat(f"{inicio}-01")
    ultimo = date.fromisoformat(f"{fim}-01")
    indice_primeiro = primeiro.year * 12 + primeiro.month - 1
    indice_ultimo = ultimo.year * 12 + ultimo.month - 1
    if indice_primeiro > indice_ultimo:
        raise SystemExit("A competência inicial não pode ser posterior à final.")
    return [
        f"{indice // 12:04d}-{indice % 12 + 1:02d}"
        for indice in range(indice_primeiro, indice_ultimo + 1)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
