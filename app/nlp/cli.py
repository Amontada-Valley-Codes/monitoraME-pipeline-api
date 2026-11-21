"""Ferramentas locais do NLP, sem NestJS ou autenticação."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core import orm
from app.nlp.corpus import sincronizar_corpus
from app.nlp.profiles.schemas import MatchingBatchRequest, SCHEMA_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.nlp.cli")
    parser.add_argument("command", choices=["validate-fixtures", "sync-corpus", "run-matching", "enqueue-matching", "matching-results", "matching-status", "matching-batch-results"])
    parser.add_argument(
        "--fixtures",
        type=Path,
        help="Arquivo JSON explícito para validação ou execução local com perfis de teste.",
    )
    parser.add_argument("--page-size", type=int, default=200, help="Oportunidades por página na leitura do Analytics.")
    parser.add_argument("--dry-run", action="store_true", help="Calcula alterações do corpus sem persistir.")
    parser.add_argument("--batch-id", help="UUID estável para repetição idempotente de run-matching.")
    parser.add_argument("--company-id", help="UUID usado por matching-results.")
    parser.add_argument("--eligible-only", action="store_true", help="Filtra matching-results para eligibility=ELIGIBLE.")
    parser.add_argument("--compatible-only", action="store_true", help="Filtra matching-results para compatibility_level MEDIUM ou HIGH.")
    parser.add_argument("--k", type=int, help="Candidatos Top-K por perfil; usa NLP_RETRIEVAL_TOP_K por padrão.")
    parser.add_argument("--limit", type=int, default=100, help="Máximo de resultados consultados.")
    parser.add_argument("--cursor", help="Cursor opaco para paginação de resultados do batch.")
    args = parser.parse_args()

    if args.command in {"validate-fixtures", "run-matching", "enqueue-matching"} and args.fixtures is None:
        parser.error(f"{args.command} exige --fixtures; o matching de produção recebe perfis pela API interna.")

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

    if args.command == "matching-results":
        if not args.company_id:
            parser.error("matching-results exige --company-id")
        try:
            company_id = str(UUID(args.company_id))
        except ValueError:
            parser.error("--company-id deve ser UUID")
        if not 1 <= args.limit <= 500:
            parser.error("--limit deve estar entre 1 e 500")
        from app.nlp.matching.pipeline import consultar_resultados_empresa
        results = consultar_resultados_empresa(company_id, limit=args.limit)
        if args.eligible_only:
            results = [item for item in results if item.get("eligibility") == "ELIGIBLE"]
        if args.compatible_only:
            results = [item for item in results if item.get("compatibility_level") in {"MEDIUM", "HIGH"}]
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.command == "matching-status":
        if not args.batch_id:
            parser.error("matching-status exige --batch-id")
        from app.nlp.matching.pipeline import consultar_status_batch
        try:
            batch_id = str(UUID(args.batch_id))
        except ValueError:
            parser.error("--batch-id deve ser UUID")
        status = consultar_status_batch(batch_id)
        if status is None:
            print(json.dumps({"error": "batch_not_found", "batch_id": batch_id}))
            return 1
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    if args.command == "matching-batch-results":
        if not args.batch_id:
            parser.error("matching-batch-results exige --batch-id")
        try:
            batch_id = str(UUID(args.batch_id))
        except ValueError:
            parser.error("--batch-id deve ser UUID")
        if not 1 <= args.limit <= 500:
            parser.error("--limit deve estar entre 1 e 500")
        from app.nlp.matching.pipeline import consultar_resultados_batch
        try:
            page = consultar_resultados_batch(batch_id, limit=args.limit, cursor=args.cursor)
        except ValueError as exc:
            parser.error(str(exc))
        if page is None:
            print(json.dumps({"error": "batch_not_found", "batch_id": batch_id}))
            return 1
        print(json.dumps(page, ensure_ascii=False, indent=2))
        return 0

    if args.command in {"run-matching", "enqueue-matching"}:
        try:
            companies = json.loads(args.fixtures.read_text(encoding="utf-8"))
            batch = MatchingBatchRequest(
                batch_id=uuid4(), schema_version=SCHEMA_VERSION, companies=companies
            )
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            print(f"Fixtures inválidas ({type(exc).__name__}).", file=sys.stderr)
            return 1
        try:
            batch_id = str(UUID(args.batch_id)) if args.batch_id else str(batch.batch_id)
        except ValueError:
            parser.error("--batch-id deve ser UUID")
        if args.command == "enqueue-matching":
            from app.nlp.repositories.matching import enfileirar_batch_session
            with orm.main_session() as session:
                queued, created = enfileirar_batch_session(
                    session, batch_id=batch_id, profiles=batch.companies,
                )
            print(json.dumps({
                "batch_id": queued.batch_id, "status": queued.status.lower(),
                "created": created, "accepted_companies": queued.total_companies,
            }, ensure_ascii=False, indent=2))
            return 0
        from app.nlp.embeddings.model import E5EmbeddingModel
        from app.nlp.matching.pipeline import consultar_status_batch, executar_matching_local
        results, created = executar_matching_local(
            batch.companies, batch_id=batch_id, model=E5EmbeddingModel(), k=args.k,
        )
        status = consultar_status_batch(batch_id)
        print(json.dumps({
            "batch_id": batch_id, "created": created, "status": status,
            "profiles": len(batch.companies), "results": [item.model_dump(mode="json") for item in results],
        }, ensure_ascii=False, indent=2))
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
