"""Pontuação determinística, com evidências e confiança de cobertura."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.nlp.matching.schemas import MatchingEvidence, ProcurementProfile
from app.nlp.profiles.schemas import CompanyProfile


WEIGHTS: dict[str, int] = {
    "category": 30,
    "object": 25,
    "cnae": 20,
    "region": 15,
    "operations": 10,
}
assert sum(WEIGHTS.values()) == 100


@dataclass(frozen=True)
class TaxonomyAssociation:
    category: str
    relation_type: str
    confidence: float
    condition: str | None = None


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    data_confidence: float
    evidence: tuple[MatchingEvidence, ...]
    reason_codes: tuple[str, ...]
    warning_codes: tuple[str, ...]


def classificar_score(score: float) -> str:
    if score < 50:
        return "LOW"
    if score < 75:
        return "MEDIUM"
    return "HIGH"


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return {token for token in re.findall(r"[\w]+", normalized) if len(token) > 1}


def _similaridade(texto_a: str, texto_b: str) -> float:
    a, b = _tokens(texto_a), _tokens(texto_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _termos_comuns(texto_a: str, texto_b: str) -> list[str]:
    return sorted(_tokens(texto_a) & _tokens(texto_b))[:20]


def calcular_score(
    company: CompanyProfile,
    opportunity: ProcurementProfile,
    *,
    taxonomy_version: str,
    cnae_categories: dict[str, list[TaxonomyAssociation]],
) -> ScoreBreakdown:
    """Calcula score 0-100; critérios sem entrada não reduzem compatibilidade."""
    values: dict[str, float] = {}
    details: dict[str, str] = {}
    # Categorias confirmadas pelo perfil são independentes da inferência por CNAE.
    company_categories = set(company.categories)
    opportunity_categories = set(opportunity.categories)
    if opportunity_categories and company_categories:
        values["category"] = len(opportunity_categories & company_categories) / len(opportunity_categories | company_categories)
        matched = sorted(opportunity_categories & company_categories)
        details["category"] = f"Categorias coincidentes: {', '.join(matched) or 'nenhuma'}; categorias do edital: {', '.join(sorted(opportunity_categories))}."

    profile_text = " ".join(c.description or "" for c in company.cnaes).strip()
    opportunity_text = " ".join(filter(None, [opportunity.object, *opportunity.items])).strip()
    if profile_text and opportunity_text:
        values["object"] = _similaridade(profile_text, opportunity_text)
        terms = _termos_comuns(profile_text, opportunity_text)
        details["object"] = f"Similaridade lexical determinística; termos coincidentes: {', '.join(terms) or 'nenhum'} ({len(_tokens(opportunity_text))} termos no edital)."

    associations: dict[str, TaxonomyAssociation] = {}
    conditional_categories: set[str] = set()
    conditional_details: dict[str, str | None] = {}
    for cnae in company.cnaes:
        for association in cnae_categories.get(cnae.code, []):
            if association.relation_type == "CONDICIONAL":
                conditional_categories.add(association.category)
                conditional_details[association.category] = association.condition
                continue
            current = associations.get(association.category)
            if current is None or association.confidence > current.confidence:
                associations[association.category] = association
    mapped_categories = set(associations)
    if opportunity_categories and mapped_categories:
        matched = sorted(opportunity_categories & mapped_categories)
        values["cnae"] = sum(associations[category].confidence for category in matched) / len(opportunity_categories | mapped_categories)
        provenance = ", ".join(
            f"{category} ({associations[category].relation_type}, confiança {associations[category].confidence:.2f})"
            for category in matched
        ) or "nenhuma"
        unresolved = sorted(conditional_categories & opportunity_categories)
        unresolved_text = ", ".join(
            f"{category} (CONDICIONAL: {conditional_details[category] or 'condição não informada'})"
            for category in unresolved
        )
        suffix = f" Relações não avaliadas: {unresolved_text}." if unresolved else ""
        details["cnae"] = f"Taxonomia {taxonomy_version}; associações coincidentes: {provenance}.{suffix}"

    territory_known = opportunity.territory_states is not None or opportunity.territory_municipalities is not None
    if territory_known:
        state_match = opportunity.territory_states is None or company.location.state in opportunity.territory_states
        municipality_match = opportunity.territory_municipalities is None or company.location.municipality_code in opportunity.territory_municipalities
        values["region"] = float(state_match and municipality_match)
        details["region"] = f"Localização do perfil: {company.location.state}/{company.location.municipality_code}."

    shared_preferences = set(company.operational_preferences) & set(opportunity.operational_requirements)
    if shared_preferences:
        matches = sum(company.operational_preferences[key] == opportunity.operational_requirements[key] for key in shared_preferences)
        values["operations"] = matches / len(shared_preferences)
        details["operations"] = "Preferências comparadas: " + ", ".join(sorted(shared_preferences))

    coverage = sum(WEIGHTS[key] for key in values)
    score = (sum(WEIGHTS[key] * value for key, value in values.items()) / coverage * 100) if coverage else 0.0
    confidence = float(coverage)
    evidence = tuple(MatchingEvidence(criterion=key, score=round(value, 6), details=details[key]) for key, value in values.items())
    reasons = tuple(f"{key.upper()}_MATCH" if value > 0 else f"{key.upper()}_NO_MATCH" for key, value in values.items())
    warnings = [f"{key.upper()}_DATA_MISSING" for key in WEIGHTS if key not in values]
    if conditional_categories & opportunity_categories:
        warnings.append("CNAE_CONDITIONAL_UNRESOLVED")
    # Score alto exige pelo menos uma evidência concreta nos critérios de atividade.
    activity_evidence = any(key in values for key in ("category", "object", "cnae"))
    if score >= 75 and not activity_evidence:
        score = 74.999999
        warnings.append("HIGH_WITHOUT_ACTIVITY_EVIDENCE")
    return ScoreBreakdown(round(score, 6), confidence, evidence, reasons, tuple(warnings))
