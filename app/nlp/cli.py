"""Ferramentas locais do NLP, sem NestJS ou autenticação."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from app.core import orm
from app.nlp.corpus import sincronizar_corpus
from app.nlp.profiles.schemas import MatchingBatchRequest, SCHEMA_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nlp.cli")
    parser.add_argument("command", choices=["validate-fixtures", "sync-corpus"])
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "tests" / "nlp" / "fixtures" / "companies.json",
        help="Arquivo JSON no formato de lista de CompanyProfile.",
    )
    parser.add_argument("--page-size", type=int, default=200, help="Oportunidades por página na leitura do Analytics.")
    parser.add_argument("--dry-run", action="store_true", help="Calcula alterações do corpus sem persistir.")
    args = parser.parse_args()

    if args.command == "sync-corpus":
        try:
            with orm.main_session() as session:
                resultado = sincronizar_corpus(session, page_size=args.page_size, dry_run=args.dry_run)
        except Exception as exc:
            # A mensagem do driver pode conter host, usuário ou outros detalhes da conexão.
            if "password authentication failed" in str(exc).lower():
                print(
                    "Autenticação recusada pelo PostgreSQL Analytics. Confira o usuário e a senha de DATABASE_URL no .env e valide-os contra as credenciais efetivas do banco local.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Falha ao sincronizar corpus Analytics ({type(exc).__name__}). Confira a conexão e se a migration principal está aplicada.",
                    file=sys.stderr,
                )
            return 1
        resumo = asdict(resultado)
        resumo["dry_run"] = args.dry_run
        print(json.dumps(resumo, ensure_ascii=False, indent=2))
        return 0

    try:
        companies = json.loads(args.fixtures.read_text(encoding="utf-8"))
        batch = MatchingBatchRequest(
            batch_id=uuid4(), schema_version=SCHEMA_VERSION, companies=companies
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Não foi possível ler fixtures: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            field = ".".join(str(part) for part in error["loc"]) or "contrato"
            print(f"{field}: {error['msg']}", file=sys.stderr)
        return 1

    print(json.dumps({
        "valid": True,
        "schema_version": batch.schema_version,
        "companies": len(batch.companies),
        "company_sizes": sorted({company.company_size.value for company in batch.companies}),
        "total_cnaes": sum(len(company.cnaes) for company in batch.companies),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
