#!/usr/bin/env python3
"""Atualiza classificações de órgãos dos municípios/exercícios já publicados."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core import database, orm
from app.core.models import TceDespesaIngestionRun, TceEmpenho
from app.pipeline.ingestion import tce
from app.pipeline.persistence.tce_orgaos import salvar_classificacoes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--municipio", help="Código TCE de três dígitos; sem este argumento processa todos.")
    args = parser.parse_args()
    database.init_db()
    consulta = (
        select(TceDespesaIngestionRun.codigo_municipio_tce, TceEmpenho.exercicio_orcamento)
        .join(TceEmpenho, TceEmpenho.run_id == TceDespesaIngestionRun.id)
        .where(TceDespesaIngestionRun.status == "PUBLICADO")
        .distinct()
        .order_by(TceDespesaIngestionRun.codigo_municipio_tce, TceEmpenho.exercicio_orcamento)
    )
    if args.municipio:
        consulta = consulta.where(TceDespesaIngestionRun.codigo_municipio_tce == args.municipio.strip())
    with orm.main_session() as session:
        escopos = session.execute(consulta).all()

    for municipio, exercicio in escopos:
        cadastro = tce.buscar_documentos_exercicio(
            "orgaos", codigo_municipio=str(municipio), exercicio=int(exercicio) // 100,
        )
        with orm.main_session() as session:
            classificados = salvar_classificacoes(
                session, codigo_municipio_tce=str(municipio),
                exercicio_orcamento=int(exercicio), orgaos=cadastro,
            )
        print(f"{municipio}/{int(exercicio) // 100}: {len(classificados)} órgãos classificados")
    print(f"Escopos atualizados: {len(escopos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
