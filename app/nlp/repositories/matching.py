"""Persistência idempotente de batches locais e resultados do matching."""
from __future__ import annotations

from hashlib import sha256
import base64
import json
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import NlpMatchingBatch, NlpMatchingResult
from app.nlp.matching.schemas import MatchingResult
from app.nlp.profiles.schemas import CompanyProfile
from app.pncp.jobs import NovoJob, agendar_job_session


def _profile_payload(profile: CompanyProfile) -> dict[str, object]:
    payload = profile.model_dump(mode="json")
    # Preserve the pre-extension canonical payload/hash for legacy profiles.
    optional_empty_fields = (
        "business_description", "version", "schema_version", "products_and_services",
        "regions", "modalities",
    )
    for field in optional_empty_fields:
        if not getattr(profile, field):
            payload.pop(field, None)
    return payload


def hash_payload(profiles: Sequence[CompanyProfile]) -> str:
    payload = [_profile_payload(profile) for profile in profiles]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def registrar_batch_session(
    session: Session, *, batch_id: str, profiles: Sequence[CompanyProfile],
) -> tuple[NlpMatchingBatch, bool]:
    payload_hash = hash_payload(profiles)
    existing = session.get(NlpMatchingBatch, batch_id)
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise ValueError("batch_id já existe associado a outro payload")
        if existing.profiles is None:
            existing.profiles = [_profile_payload(profile) for profile in profiles]
        return existing, False
    batch = NlpMatchingBatch(
        batch_id=batch_id, payload_hash=payload_hash, status="RUNNING",
        profiles=[_profile_payload(profile) for profile in profiles],
        total_companies=len(profiles), processed_companies=0, failed_companies=0,
    )
    session.add(batch)
    session.flush()
    return batch, True


def enfileirar_batch_session(
    session: Session, *, batch_id: str, profiles: Sequence[CompanyProfile],
) -> tuple[NlpMatchingBatch, bool]:
    payload_hash = hash_payload(profiles)
    batch = session.get(NlpMatchingBatch, batch_id)
    if batch is not None and batch.payload_hash != payload_hash:
        raise ValueError("batch_id já existe associado a outro payload")
    if batch is not None and batch.status in {"PENDING", "RUNNING", "COMPLETED"}:
        return batch, False
    if batch is None:
        batch = NlpMatchingBatch(
            batch_id=batch_id, payload_hash=payload_hash,
            profiles=[_profile_payload(profile) for profile in profiles],
            status="PENDING", total_companies=len(profiles),
            processed_companies=0, failed_companies=0,
        )
        session.add(batch)
        session.flush()
    else:
        batch.profiles = [_profile_payload(profile) for profile in profiles]
        batch.status = "PENDING"
        batch.total_companies = len(profiles)
        batch.processed_companies = 0
        batch.failed_companies = 0
        batch.updated_at = datetime.now(timezone.utc)
    _, created = agendar_job_session(session, NovoJob(
        job_type="nlp_matching_batch",
        entity_type="MATCHING_BATCH",
        source="MATCHING",
        scope_key=batch_id,
        dedupe_key=f"nlp-matching-batch:{batch_id}",
        priority=80,
        next_run_at=datetime.now(timezone.utc),
        max_attempts=5,
    ))
    return batch, created


def preparar_batch_reexecucao_session(session: Session, *, batch_id: str) -> None:
    batch = session.get(NlpMatchingBatch, batch_id)
    if batch is None:
        raise ValueError("batch não registrado")
    # Preserve prior snapshots. The result primary key includes all input/model
    # versions, so replaying the same snapshot is an idempotent upsert while a
    # newer profile/procurement version remains independently queryable.
    batch.status = "RUNNING"
    batch.processed_companies = 0
    batch.failed_companies = 0
    batch.updated_at = datetime.now(timezone.utc)


def salvar_resultados_session(
    session: Session, *, batch_id: str, results: Sequence[MatchingResult],
    total_companies: int, failed_companies: int = 0, finalize: bool = True,
) -> None:
    for result in results:
        row = {
            "batch_id": batch_id,
            "company_id": str(result.company_id),
            "source": result.edital.source,
            "external_id": result.edital.external_id,
            "profile_version": result.profile_version,
            "procurement_version": result.edital.version,
            "model_version": result.model_version or "unknown",
            "matching_version": result.matching_version,
            "taxonomy_version": result.taxonomy_version,
            "score": result.score,
            "eligibility": result.eligibility.value,
            "result": result.model_dump(mode="json"),
        }
        stmt = insert(NlpMatchingResult).values(**row)
        update = {key: stmt.excluded[key] for key in ("score", "eligibility", "result", "processed_at")}
        session.execute(stmt.on_conflict_do_update(
            index_elements=[
                "batch_id", "company_id", "source", "external_id", "profile_version",
                "procurement_version", "model_version", "matching_version", "taxonomy_version",
            ], set_=update,
        ))
    if not finalize:
        return
    batch = session.get(NlpMatchingBatch, batch_id)
    if batch is None:
        raise ValueError("batch não registrado")
    batch.status = "PARTIAL" if failed_companies else "COMPLETED"
    batch.processed_companies = total_companies - failed_companies
    batch.failed_companies = failed_companies
    batch.updated_at = datetime.now(timezone.utc)


def listar_resultados_session(
    session: Session, *, company_id: str, limit: int = 100,
) -> list[dict[str, object]]:
    if limit < 1 or limit > 500:
        raise ValueError("limit deve estar entre 1 e 500")
    rows = session.scalars(
        select(NlpMatchingResult)
        .join(NlpMatchingBatch, NlpMatchingBatch.batch_id == NlpMatchingResult.batch_id)
        .where(NlpMatchingResult.company_id == company_id)
        .where(NlpMatchingBatch.status.in_(("COMPLETED", "PARTIAL")))
        .order_by(NlpMatchingResult.processed_at.desc(), NlpMatchingResult.score.desc())
        .limit(limit)
    )
    return [row.result for row in rows]


def listar_resultados_filtrados_session(
    session: Session,
    *,
    company_id: str | None = None,
    batch_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, object]], int]:
    """Consulta resultados HIGH persistidos, antes da contagem e paginação."""
    if not 1 <= limit <= 500:
        raise ValueError("limit deve estar entre 1 e 500")
    if offset < 0:
        raise ValueError("offset deve ser maior ou igual a zero")
    filtros = [
        NlpMatchingResult.result["compatibility_level"].as_string() == "HIGH",
        NlpMatchingBatch.status.in_(("COMPLETED", "PARTIAL")),
    ]
    if company_id is not None:
        filtros.append(NlpMatchingResult.company_id == company_id)
    if batch_id is not None:
        filtros.append(NlpMatchingResult.batch_id == batch_id)

    total = int(session.scalar(
        select(func.count()).select_from(NlpMatchingResult)
        .join(NlpMatchingBatch, NlpMatchingBatch.batch_id == NlpMatchingResult.batch_id)
        .where(*filtros)
    ) or 0)
    rows = session.scalars(
        select(NlpMatchingResult)
        .join(NlpMatchingBatch, NlpMatchingBatch.batch_id == NlpMatchingResult.batch_id)
        .where(*filtros)
        .order_by(
            NlpMatchingResult.processed_at.desc(),
            NlpMatchingResult.score.desc(),
            NlpMatchingResult.company_id,
            NlpMatchingResult.external_id,
        )
        .limit(limit)
        .offset(offset)
    )
    return ([
        {
            "batch_id": row.batch_id,
            "company_id": row.company_id,
            "compatibility_level": row.result["compatibility_level"],
            "score": row.score,
            "processed_at": row.processed_at,
            "result": row.result,
        }
        for row in rows
    ], total)


def listar_resultados_batch_session(
    session: Session, *, batch_id: str, limit: int = 100, cursor: str | None = None,
    high_only: bool = False,
) -> tuple[list[dict[str, object]], str | None]:
    if not 1 <= limit <= 500:
        raise ValueError("limit deve estar entre 1 e 500")
    statement = (
        select(NlpMatchingResult)
        .join(NlpMatchingBatch, NlpMatchingBatch.batch_id == NlpMatchingResult.batch_id)
        .where(NlpMatchingResult.batch_id == batch_id)
        .where(NlpMatchingBatch.status.in_(("COMPLETED", "PARTIAL")))
    )
    if high_only:
        statement = statement.where(
            NlpMatchingResult.result["compatibility_level"].as_string() == "HIGH"
        )
    if cursor:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            company, score, external = json.loads(raw)
            score = float(score)
            if not isinstance(company, str) or not isinstance(external, str):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("cursor invalido") from exc
        statement = statement.where(or_(
            NlpMatchingResult.score < score,
            and_(NlpMatchingResult.score == score, NlpMatchingResult.company_id > company),
            and_(NlpMatchingResult.score == score, NlpMatchingResult.company_id == company,
                 NlpMatchingResult.external_id > external),
        ))
    rows = list(session.scalars(statement.order_by(
        NlpMatchingResult.score.desc(), NlpMatchingResult.company_id,
        NlpMatchingResult.external_id,
    ).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            [last.company_id, last.score, last.external_id], separators=(",", ":")
        ).encode()).decode().rstrip("=")
    return [row.result for row in rows], next_cursor


def marcar_batch_falha_session(session: Session, *, batch_id: str, payload_hash: str) -> None:
    batch = session.get(NlpMatchingBatch, batch_id)
    if batch is not None and batch.payload_hash == payload_hash and batch.status != "COMPLETED":
        batch.status = "FAILED"
        batch.failed_companies = batch.total_companies - batch.processed_companies
        batch.updated_at = datetime.now(timezone.utc)


def consultar_batch_session(session: Session, *, batch_id: str) -> dict[str, object] | None:
    batch = session.get(NlpMatchingBatch, batch_id)
    if batch is None:
        return None
    return {
        "batch_id": batch.batch_id,
        "schema_version": "matching.v1",
        "status": batch.status.lower(),
        "total_companies": batch.total_companies,
        "processed_companies": batch.processed_companies,
        "failed_companies": batch.failed_companies,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
    }
