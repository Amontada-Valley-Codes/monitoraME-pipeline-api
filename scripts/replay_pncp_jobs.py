"""Replay consciente de jobs PNCP terminais no banco principal."""
from __future__ import annotations

import argparse
from datetime import datetime

from app.pncp.jobs import replay_jobs


def _data_hora(valor: str) -> datetime:
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use ISO-8601 com timezone") from exc
    if data.tzinfo is None:
        raise argparse.ArgumentTypeError("a data deve incluir timezone")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    seletor = parser.add_mutually_exclusive_group(required=True)
    seletor.add_argument("--job-id", type=int)
    seletor.add_argument("--flow", help="Ex.: publicacao ou atualizacao")
    seletor.add_argument("--janela", action="store_true", help="Selecione pela janela informada")
    parser.add_argument("--inicio", type=_data_hora)
    parser.add_argument("--fim", type=_data_hora)
    args = parser.parse_args()
    if args.janela and (args.inicio is None or args.fim is None):
        parser.error("--janela exige --inicio e --fim")
    if not args.janela and (args.inicio is not None or args.fim is not None):
        parser.error("--inicio/--fim exigem --janela")
    ids = replay_jobs(
        job_id=args.job_id,
        flow=args.flow,
        window_start=args.inicio if args.janela else None,
        window_end=args.fim if args.janela else None,
    )
    print({"replays": len(ids), "job_ids": ids})


if __name__ == "__main__":
    main()
