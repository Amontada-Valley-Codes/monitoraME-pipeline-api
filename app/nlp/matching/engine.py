"""Orquestra o gate de elegibilidade e o score determinístico local."""
from __future__ import annotations

from datetime import datetime, timezone

from app.nlp.matching.eligibility import avaliar_elegibilidade
from app.nlp.matching.schemas import (
    CompatibilityLevel,
    EligibilityStatus,
    MatchingReason,
    MatchingResult,
    ProcurementProfile,
    ProcurementReference,
)
from app.nlp.matching.scoring import calcular_score, classificar_score
from app.nlp.profiles.schemas import CompanyProfile


MATCHING_VERSION = "deterministic-matching-v1"


def calcular_matching(
    company: CompanyProfile,
    opportunity: ProcurementProfile,
    *,
    taxonomy_version: str,
    cnae_categories: dict[str, set[str]],
    as_of: datetime | None = None,
) -> MatchingResult:
    """Gera resultado reprodutível; `as_of` pode congelar o instante de avaliação."""
    instant = as_of or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("as_of deve incluir timezone")
    eligibility = avaliar_elegibilidade(company, opportunity, as_of=instant)

    # Gate duro ocorre antes do cálculo dos critérios de compatibilidade.
    if eligibility.status == EligibilityStatus.INELIGIBLE:
        score = 0.0
        confidence = 0.0
        evidence = []
        reasons = list(eligibility.reason_codes)
        warnings = [*eligibility.warning_codes, "SCORE_SKIPPED_INELIGIBLE"]
        level = CompatibilityLevel.LOW
    else:
        breakdown = calcular_score(
            company,
            opportunity,
            taxonomy_version=taxonomy_version,
            cnae_categories=cnae_categories,
        )
        score = breakdown.score
        confidence = breakdown.data_confidence
        evidence = list(breakdown.evidence)
        reasons = [*eligibility.reason_codes, *breakdown.reason_codes]
        warnings = [*eligibility.warning_codes, *breakdown.warning_codes]
        level = CompatibilityLevel(classificar_score(score))

    return MatchingResult(
        company_id=company.company_id,
        edital=ProcurementReference(
            procurement_id=opportunity.procurement_id,
            source=opportunity.source,
            external_id=opportunity.external_id,
            object=opportunity.object,
            url=opportunity.url,
            version=opportunity.version,
        ),
        score=score,
        data_confidence=confidence,
        eligibility=eligibility.status,
        compatibility_level=level,
        reason_codes=list(dict.fromkeys(reasons)),
        warning_codes=list(dict.fromkeys(warnings)),
        evidence=evidence,
        justifications=[MatchingReason(code=code, description=code.replace("_", " ").lower()) for code in reasons[:50]],
        profile_version=company.profile_version,
        matching_version=MATCHING_VERSION,
        taxonomy_version=taxonomy_version,
        processing_state="completed",
        processed_at=instant,
    )
