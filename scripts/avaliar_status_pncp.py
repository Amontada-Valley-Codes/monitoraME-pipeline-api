"""Avalia cenarios JSON de status PNCP sem acessar API ou banco de dados.

Uso: python scripts/avaliar_status_pncp.py < cenarios.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp.derived_status import avaliar_status


def avaliar_cenarios(payload: Any) -> list[dict[str, Any]]:
    cenarios = payload if isinstance(payload, list) else [payload]
    resultados = []
    for indice, cenario in enumerate(cenarios, start=1):
        if not isinstance(cenario, dict):
            raise ValueError(f"cenario {indice} deve ser um objeto JSON")
        agora = cenario.get("agora")
        if not agora:
            raise ValueError(f"cenario {indice} precisa informar agora com timezone")
        resultado = avaliar_status(
            official_status=cenario.get("official_status"),
            inicio=cenario.get("inicio"),
            encerramento=cenario.get("encerramento"),
            modalidade=cenario.get("modalidade"),
            forma_participacao=cenario.get("forma_participacao"),
            resultado_estado=cenario.get("resultado_estado"),
            agora=datetime.fromisoformat(agora.replace("Z", "+00:00")),
        )
        resultados.append({"caso": cenario.get("caso", f"cenario_{indice}"), **resultado})
    return resultados


def main() -> int:
    try:
        entrada = json.load(sys.stdin)
        print(json.dumps(avaliar_cenarios(entrada), ensure_ascii=False, indent=2, default=str))
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
