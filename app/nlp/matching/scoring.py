"""Pontuação híbrida com peso reduzido para objeto e evidência dos itens."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.nlp.matching.schemas import MatchingEvidence, ProcurementProfile
from app.nlp.profiles.schemas import CompanyProfile


# Objeto de compras frequentemente contém texto padronizado e pouco discriminante.
WEIGHTS: dict[str, int] = {"category": 20, "object": 10, "complementary": 20, "items": 35, "cnae": 15}
PREFERENCE_WEIGHTS: dict[str, int] = {"region": 50, "modality": 25, "operations": 25}
SEMANTIC_SCORE_FLOOR = 0.88
GENERIC_TOKENS = {
    "servico", "servicos", "comum", "comuns", "engenharia", "manutencao", "reparacao",
    "reparo", "aquisicao", "contratacao", "contratar", "fornecimento", "fornecer",
    "prestacao", "objeto", "licitacao", "edital", "item", "itens",
    "empresa", "publica", "publico", "geral", "diversos", "diversas", "apoio",
    "execucao", "realizacao", "eventual", "futura", "futuro", "necessidade",
}
assert sum(WEIGHTS.values()) == 100
assert sum(PREFERENCE_WEIGHTS.values()) == 100


@dataclass(frozen=True)
class TaxonomyAssociation:
    category: str
    relation_type: str
    confidence: float
    condition: str | None = None


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    preference_score: float | None
    preference_confidence: float
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
    normalized = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token for token in re.findall(r"[\w]+", normalized) if len(token) > 1}


def _tokens_especificos(text: str) -> set[str]:
    return _tokens(text) - GENERIC_TOKENS


def _similaridade(texto_a: str, texto_b: str) -> float:
    a, b = _tokens_especificos(texto_a), _tokens_especificos(texto_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _calibrar_semantica(score: float | None) -> float:
    if score is None:
        return 0.0
    return max(0.0, min(1.0, (score - SEMANTIC_SCORE_FLOOR) / (1 - SEMANTIC_SCORE_FLOOR)))


def calibrar_relevancia_semantica(score: float | None) -> float | None:
    """Converte similaridade cosseno bruta em relevância calibrada 0-100."""
    return _calibrar_semantica(score) * 100 if score is not None else None


def calcular_score(
    company: CompanyProfile,
    opportunity: ProcurementProfile,
    *,
    taxonomy_version: str,
    cnae_categories: dict[str, list[TaxonomyAssociation]],
    semantic_score: float | None = None,
    semantic_object_score: float | None = None,
    semantic_complementary_score: float | None = None,
    semantic_items_score: float | None = None,
) -> ScoreBreakdown:
    """Calcula score 0-100; critérios ausentes contribuem zero, sem renormalização."""
    values: dict[str, float] = {}
    details: dict[str, str] = {}
    warnings_extra: list[str] = []
    for name, value in (("semantic_score", semantic_score), ("semantic_object_score", semantic_object_score), ("semantic_complementary_score", semantic_complementary_score), ("semantic_items_score", semantic_items_score)):
        if value is not None and not 0 <= value <= 1:
            raise ValueError(f"{name} deve estar entre 0 e 1")
    company_categories = set(company.categories)
    opportunity_categories = set(opportunity.categories)
    if opportunity_categories and company_categories:
        values["category"] = len(opportunity_categories & company_categories) / len(opportunity_categories | company_categories)
        details["category"] = "Categorias do perfil comparadas às categorias do edital."

    descriptions = [cnae.description or "" for cnae in company.cnaes if cnae.description]
    # Compatibilidade legada para chamadas que ainda forneçam um único score.
    object_semantic = semantic_object_score if semantic_object_score is not None else semantic_score
    if opportunity.object and descriptions:
        object_terms = _tokens_especificos(opportunity.object)
        if object_terms:
            lexical = max((_similaridade(desc, opportunity.object) for desc in descriptions), default=0.0)
            semantic = _calibrar_semantica(object_semantic)
            values["object"] = max(lexical, semantic)
            details["object"] = f"Objeto (peso {WEIGHTS['object']}%); lexical={lexical:.4f}, semântico calibrado={semantic:.4f}, termos específicos={', '.join(sorted(object_terms)[:12])}."
        else:
            warnings_extra.append("OBJECT_LOW_SPECIFICITY")

    if opportunity.complementary_text and descriptions:
        lexical = max((_similaridade(desc, opportunity.complementary_text) for desc in descriptions), default=0.0)
        semantic = _calibrar_semantica(semantic_complementary_score)
        values["complementary"] = max(lexical, semantic)
        details["complementary"] = f"Informação complementar (peso {WEIGHTS['complementary']}%); lexical={lexical:.4f}, semântico calibrado={semantic:.4f}."

    if opportunity.items and descriptions:
        lexical = max(
            (_similaridade(desc, item) for desc in descriptions for item in opportunity.items if item),
            default=0.0,
        )
        semantic = _calibrar_semantica(semantic_items_score)
        values["items"] = max(lexical, semantic)
        details["items"] = f"Itens (peso {WEIGHTS['items']}%); melhor correspondência lexical={lexical:.4f}, semântico calibrado={semantic:.4f}; {len(opportunity.items)} itens avaliados."

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
        values["cnae"] = sum(associations[c].confidence for c in matched) / len(opportunity_categories | mapped_categories)
        details["cnae"] = f"Taxonomia {taxonomy_version}; categorias coincidentes: {', '.join(matched) or 'nenhuma'}."

    preferences: dict[str, float] = {}
    preference_details: dict[str, str] = {}
    if company.interested_states is not None or company.interested_municipalities is not None:
        states, municipalities = set(opportunity.territory_states or []), set(opportunity.territory_municipalities or [])
        comparisons = []
        if company.interested_states and states:
            comparisons.append(float(bool(set(company.interested_states) & states)))
        if company.interested_municipalities and municipalities:
            comparisons.append(float(bool(set(company.interested_municipalities) & municipalities)))
        if comparisons:
            preferences["region"] = sum(comparisons) / len(comparisons)
            preference_details["region"] = "Territórios de interesse comparados à abrangência do edital."
    if company.preferred_modality_codes and opportunity.modality_code:
        preferences["modality"] = float(opportunity.modality_code in company.preferred_modality_codes)
        preference_details["modality"] = f"Modalidade {opportunity.modality_code} comparada às preferências."
    shared_preferences = set(company.operational_preferences) & set(opportunity.operational_requirements)
    if shared_preferences:
        preferences["operations"] = sum(company.operational_preferences[k] == opportunity.operational_requirements[k] for k in shared_preferences) / len(shared_preferences)
        preference_details["operations"] = "Preferências operacionais: " + ", ".join(sorted(shared_preferences))

    coverage = sum(WEIGHTS[key] for key in values)
    score = sum(WEIGHTS[key] * value for key, value in values.items())
    preference_coverage = sum(PREFERENCE_WEIGHTS[key] for key in preferences)
    preference_score = sum(PREFERENCE_WEIGHTS[key] * value for key, value in preferences.items()) / preference_coverage * 100 if preference_coverage else None
    evidence = tuple(
        [MatchingEvidence(criterion=k, score=round(v, 6), details=details[k]) for k, v in values.items()]
        + [MatchingEvidence(criterion=f"preference_{k}", score=round(v, 6), details=preference_details[k]) for k, v in preferences.items()]
    )
    reasons = tuple(f"{k.upper()}_MATCH" if v > 0 else f"{k.upper()}_NO_MATCH" for k, v in values.items())
    warnings = [f"{k.upper()}_DATA_MISSING" for k in WEIGHTS if k not in values]
    warnings.extend(warnings_extra)
    warnings.extend(f"PREFERENCE_{k.upper()}_NOT_COMPARABLE" for k in PREFERENCE_WEIGHTS if k not in preferences)
    if conditional_categories & opportunity_categories:
        warnings.append("CNAE_CONDITIONAL_UNRESOLVED")
    if score >= 75 and coverage < 50:
        score = 74.999999
        warnings.append("HIGH_WITH_LOW_EVIDENCE_COVERAGE")
    return ScoreBreakdown(round(score, 6), preference_score, float(preference_coverage), float(coverage), evidence, reasons, tuple(warnings))
