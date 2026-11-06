"""Validacao e expansao de referencias mensais."""

from __future__ import annotations

from datetime import date
import re


def referencias_entre(inicio: str, fim: str) -> list[str]:
    """Retorna competencias mensais inclusivas no formato ``YYYY-MM``."""

    def indice(valor: str) -> int:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", valor):
            raise ValueError("Referencia deve usar YYYY-MM.")
        data = date.fromisoformat(valor + "-01")
        return data.year * 12 + data.month - 1

    primeiro, ultimo = indice(inicio), indice(fim)
    if primeiro > ultimo:
        raise ValueError("Referencia inicial posterior a final.")
    return [f"{item // 12:04d}-{item % 12 + 1:02d}" for item in range(primeiro, ultimo + 1)]


__all__ = ["referencias_entre"]
