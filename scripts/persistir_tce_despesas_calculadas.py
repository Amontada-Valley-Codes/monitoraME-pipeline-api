#!/usr/bin/env python3
"""Publica um resultado calculado de despesas TCE a partir de um JSON local."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from app.pipeline.persistence.tce_despesas_calculadas import (
    publicar_resultados_calculados,
)


def _carregar(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Nao foi possivel ler o contrato JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("A raiz do contrato deve ser um objeto JSON.")
    if not isinstance(payload.get("registros"), list):
        raise ValueError("registros deve ser uma lista JSON.")
    if payload.get("fontes") is not None and not isinstance(payload["fontes"], dict):
        raise ValueError("fontes deve ser um objeto JSON.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arquivo", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = _carregar(args.arquivo)
        resultado = publicar_resultados_calculados(
            codigo_municipio_tce=str(payload["codigo_municipio_tce"]),
            exercicio_orcamento=int(payload["exercicio_orcamento"]),
            algoritmo_versao=str(payload["algoritmo_versao"]),
            registros=payload["registros"],
            fontes=payload.get("fontes") or {},
        )
    except (KeyError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "REJEITADO",
                    "erro": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2
    print(json.dumps(asdict(resultado), ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
