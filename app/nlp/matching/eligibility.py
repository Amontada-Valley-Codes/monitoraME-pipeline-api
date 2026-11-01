"""Gate de elegibilidade; ausência de dado é distinta de reprovação."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.nlp.matching.schemas import EligibilityStatus, ProcurementProfile
from app.nlp.profiles.schemas import CompanyProfile


@dataclass(frozen=True)
class EligibilityDecision:
    status: EligibilityStatus
    reason_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]


_OPEN_STATUSES = {"ABERTA", "OPEN", "RECEBENDO_PROPOSTAS"}
_CLOSED_STATUSES = {"FECHADA", "CLOSED", "ENCERRADA", "HOMOLOGADA", "DESERTA"}


def avaliar_elegibilidade(
    company: CompanyProfile,
    opportunity: ProcurementProfile,
    *,
    as_of: datetime | None = None,
) -> EligibilityDecision:
    """Aplica regras conhecidas sem tratar ausência como incompatibilidade."""
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of deve incluir timezone")
    reasons: list[str] = []
    warnings: list[str] = []

    if opportunity.revoked is True or opportunity.annulled is True or opportunity.suspended is True:
        reasons.append("OPPORTUNITY_INVALIDATED")
    if opportunity.revoked is None or opportunity.annulled is None or opportunity.suspended is None:
        warnings.append("INVALIDATION_STATUS_INCOMPLETE")

    status = (opportunity.status or "").strip().upper()
    if status in _CLOSED_STATUSES:
        reasons.append("OPPORTUNITY_CLOSED")
    elif status not in _OPEN_STATUSES:
        warnings.append("OPEN_STATUS_UNCONFIRMED")

    if opportunity.deadline is None:
        warnings.append("DEADLINE_MISSING")
    elif opportunity.deadline.tzinfo is None:
        warnings.append("DEADLINE_TIMEZONE_MISSING")
    elif opportunity.deadline <= now:
        reasons.append("DEADLINE_EXPIRED")

    if opportunity.accepted_company_sizes is None:
        warnings.append("COMPANY_SIZE_UNCONFIRMED")
    elif company.company_size.value not in opportunity.accepted_company_sizes:
        reasons.append("COMPANY_SIZE_NOT_ACCEPTED")

    if opportunity.territory_states is None and opportunity.territory_municipalities is None:
        warnings.append("TERRITORY_UNCONFIRMED")
    else:
        state_ok = opportunity.territory_states is None or company.location.state in opportunity.territory_states
        municipality_ok = opportunity.territory_municipalities is None or company.location.municipality_code in opportunity.territory_municipalities
        if not (state_ok and municipality_ok):
            reasons.append("TERRITORY_NOT_ACCEPTED")

    if opportunity.participation_form is None or company.participation_forms is None:
        warnings.append("PARTICIPATION_FORM_UNCONFIRMED")
    elif opportunity.participation_form not in company.participation_forms:
        reasons.append("PARTICIPATION_FORM_NOT_ACCEPTED")

    if reasons:
        result = EligibilityStatus.INELIGIBLE
    elif warnings:
        result = EligibilityStatus.NOT_CONFIRMED
    else:
        result = EligibilityStatus.ELIGIBLE
    return EligibilityDecision(result, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(warnings)))
