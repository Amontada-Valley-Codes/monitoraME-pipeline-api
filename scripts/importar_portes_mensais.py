"""Importa publicacoes verificadas do piloto no PostgreSQL."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.portes_historicos import importar

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--resultado", required=True)
    p.add_argument("--workspace", default=".")
    a = p.parse_args()
    print(json.dumps(importar(a.resultado, workspace=a.workspace), ensure_ascii=False, indent=2))
