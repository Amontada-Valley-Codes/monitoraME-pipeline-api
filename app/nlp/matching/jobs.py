"""Agendamento idempotente de matching após uma nova versão de perfil."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy.orm import Session

from app.nlp.profiles.schemas import CompanyProfile
from app.pncp.jobs import NovoJob, agendar_job_session


def agendar_recalculo_perfil_session(
    session: Session,
    profile: CompanyProfile,
    *,
    now: datetime | None = None,
) -> tuple[int, bool]:
    """Deduplica por empresa, versão de perfil e regra do motor."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now deve incluir timezone")
    identidade = f"{profile.company_id}:{profile.profile_version}:deterministic-matching-v1"
    dedupe_key = "matching-profile:" + sha256(identidade.encode("utf-8")).hexdigest()
    return agendar_job_session(session, NovoJob(
        job_type="nlp_matching",
        entity_type="COMPANY_PROFILE",
        source="MATCHING",
        scope_key=identidade,
        dedupe_key=dedupe_key,
        priority=80,
        next_run_at=instant,
        max_attempts=5,
    ))
