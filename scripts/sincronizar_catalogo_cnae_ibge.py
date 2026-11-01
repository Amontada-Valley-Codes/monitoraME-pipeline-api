"""Sincroniza o catálogo CNAE-Subclasses do IBGE para busca e métricas PNCP."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import orm
from app.pncp.cnae_ibge import baixar_catalogo_ibge, salvar_catalogo_session


def main() -> None:
    registros = baixar_catalogo_ibge()
    with orm.main_session() as session:
        total = salvar_catalogo_session(session, registros)
    print(f"Catálogo CNAE do IBGE sincronizado: {total} subclasses processadas.")


if __name__ == "__main__":
    main()
