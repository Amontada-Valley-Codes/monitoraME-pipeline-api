"""Exporta evidencias locais PNCP; nao consulta fonte nem altera registros."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import orm
from app.pncp.quality import consultar_qualidade_session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--endpoint")
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    with orm.main_session() as session:
        relatorio = consultar_qualidade_session(session, run_id=args.run_id, endpoint=args.endpoint)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
