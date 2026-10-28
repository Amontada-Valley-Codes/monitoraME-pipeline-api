#!/usr/bin/env python3
"""Ingere empenhos e anulações do TCE-CE e depois enriquece seus fornecedores."""

from __future__ import annotations

import argparse
import subprocess


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ano", required=True, help="Ano a ingerir e enriquecer, no formato YYYY.")
    parser.add_argument(
        "--codigo-municipio",
        help="Limita a operação a um município TCE; omitido, processa todos os municípios do Ceará.",
    )
    args = parser.parse_args(argv)

    ingestao = ["tce-ingestir", "--ano", args.ano]
    enriquecimento = ["tce-enriquecer", "--ano", args.ano]
    if args.codigo_municipio:
        ingestao.extend(["--codigo-municipio", args.codigo_municipio])
        enriquecimento.extend(["--codigo-municipio", args.codigo_municipio])
    else:
        ingestao.append("--todos-municipios")

    resultado_ingestao = subprocess.run(ingestao, check=False).returncode
    if resultado_ingestao == 1:
        return 1

    resultado_enriquecimento = subprocess.run(enriquecimento, check=False).returncode
    if resultado_ingestao != 0:
        return 2
    return resultado_enriquecimento


if __name__ == "__main__":
    raise SystemExit(main())
