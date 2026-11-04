"""Orquestra o gate de elegibilidade e o score determinístico local."""
from __future__ import annotations

from datetime import datetime, timezone

from app.nlp.matching.eligibility import avaliar_elegibilidade
from app.nlp.matching.schemas import (
    CompatibilityLevel,
    EligibilityStatus,
    MatchingReason,
    MatchingEvidence,
    MatchingResult,
    ProcurementProfile,
    ProcurementReference,
)
from app.nlp.matching.scoring import TaxonomyAssociation, calcular_score, calibrar_relevancia_semantica, classificar_score
from app.nlp.profiles.schemas import CompanyProfile


MATCHING_VERSION = "hybrid-matching-v4"


def calcular_matching(
    company: CompanyProfile,
    opportunity: ProcurementProfile,
    *,
    taxonomy_version: str,
    cnae_categories: dict[str, list[TaxonomyAssociation]],
    semantic_score: float | None = None,
    semantic_object_score: float | None = None,
    semantic_complementary_score: float | None = None,
    semantic_items_score: float | None = None,
    model_version: str | None = None,
    retrieval_evidence: list[dict[str, object]] | None = None,
    additional_warnings: list[str] | None = None,
    as_of: datetime | None = None,
) -> MatchingResult:
    """Gera resultado reprodutível; `as_of` pode congelar o instante de avaliação."""
    instant = as_of or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("as_of deve incluir timezone")
    if semantic_score is not None and not 0 <= semantic_score <= 1:
        raise ValueError("semantic_score deve estar entre 0 e 1")
    if semantic_score is not None and not model_version:
        raise ValueError("model_version é obrigatório quando semantic_score é informado")
    eligibility = avaliar_elegibilidade(company, opportunity, as_of=instant)

    # Gate duro ocorre antes do cálculo dos critérios de compatibilidade.
    if eligibility.status == EligibilityStatus.INELIGIBLE:
        score = 0.0
        semantic_relevance = calibrar_relevancia_semantica(semantic_score)
        preference_score = None
        preference_confidence = 0.0
        confidence = 0.0
        evidence = list(eligibility.evidence)
        reasons = list(eligibility.reason_codes)
        warnings = [*eligibility.warning_codes, "SCORE_SKIPPED_INELIGIBLE", "LEGAL_ELIGIBILITY_NOT_VERIFIED", *(additional_warnings or [])]
        level = CompatibilityLevel.LOW
    else:
        breakdown = calcular_score(
            company,
            opportunity,
            taxonomy_version=taxonomy_version,
            cnae_categories=cnae_categories,
            semantic_score=semantic_score,
            semantic_object_score=semantic_object_score,
            semantic_complementary_score=semantic_complementary_score,
            semantic_items_score=semantic_items_score,
        )
        score = breakdown.score
        preference_score = breakdown.preference_score
        preference_confidence = breakdown.preference_confidence
        semantic_relevance = calibrar_relevancia_semantica(semantic_score)
        confidence = breakdown.data_confidence
        evidence = [*eligibility.evidence, *breakdown.evidence]
        evidence.extend(
            MatchingEvidence(
                criterion="retrieval_segment",
                score=(calibrar_relevancia_semantica(max(0.0, min(1.0, float(item.get("score", 0.0))))) or 0.0) / 100,
                details=(
                    f"Raw cosine={float(item.get('score', 0.0)):.4f}; displayed score is calibrated evidence, not compatibility. "
                    f"Similaridade cosseno de recuperação (não é o score final); CNAE {item.get('cnae')} ({str(item.get('cnae_description') or 'descrição indisponível')[:180]}); "
                    f"segmento {item.get('segment_type')} "
                    f"({item.get('segment_key')}); evidência: {str(item.get('snippet') or 'texto não carregado')[:220]}"
                ),
            )
            for item in (retrieval_evidence or [])[:10]
        )
        reasons = [*eligibility.reason_codes, *breakdown.reason_codes]
        warnings = [*eligibility.warning_codes, *breakdown.warning_codes, "LEGAL_ELIGIBILITY_NOT_VERIFIED", *(additional_warnings or [])]
        level = CompatibilityLevel(classificar_score(score))

    if eligibility.status == EligibilityStatus.INELIGIBLE:
        preference_score = None

    evidence_by_criterion = {item.criterion: item.details for item in evidence}
    justifications = []
    for code in dict.fromkeys(reasons):
        if code.startswith("DEADLINE_"):
            criterion = "eligibility_deadline"
        elif code.startswith("COMPANY_SIZE_"):
            criterion = "eligibility_company_size"
        elif code.startswith("OPPORTUNITY_"):
            criterion = "eligibility_status"
        else:
            criterion = code.removesuffix("_MATCH").removesuffix("_NO_MATCH").lower()
        justifications.append(MatchingReason(
            code=code,
            description=code.replace("_", " ").lower(),
            evidence=evidence_by_criterion.get(criterion),
        ))

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
        semantic_relevance=semantic_relevance,
        preference_score=preference_score,
        preference_confidence=preference_confidence,
        data_confidence=confidence,
        eligibility=eligibility.status,
        compatibility_level=level,
        reason_codes=list(dict.fromkeys(reasons)),
        warning_codes=list(dict.fromkeys(warnings)),
        evidence=evidence,
        justifications=justifications[:50],
        profile_version=company.profile_version,
        matching_version=MATCHING_VERSION,
        taxonomy_version=taxonomy_version,
        model_version=model_version,
        processing_state="completed",
        processed_at=instant,
    )
