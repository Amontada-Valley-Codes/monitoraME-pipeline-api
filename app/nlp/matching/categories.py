"""Classificação conservadora do objeto PNCP na taxonomia local."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.nlp.matching.scoring import TaxonomyAssociation
from app.nlp.profiles.schemas import CompanyProfile


CATEGORY_RULE_VERSION = "pncp-object-categories-v1"
COMPANY_CATEGORY_RULE_VERSION = "cnae-description-categories-v1"
MIN_ASSOCIATION_CONFIDENCE = 0.7

_PATTERNS: dict[str, tuple[str, ...]] = {
    "software": (
        r"\bsoftware\b", r"\bprogramas? de computador\b",
        r"\bdesenvolvimento de sistemas? (?:de informacao|informatizados?)\b",
        r"\blicenciamento de (?:sistemas?|aplicativos?)\b",
        r"\bconsultoria em tecnologia da informacao\b",
    ),
    "energia-eletrica": (
        r"\benergia eletrica\b", r"\bdistribuicao de energia\b",
        r"\bfornecimento de eletricidade\b",
    ),
    "consultoria-gestao": (
        r"\bconsultoria (?:em gestao|empresarial|administrativa)\b",
        r"\bgestao empresarial\b",
    ),
    "alimentacao": (
        r"\brefeicoes? (?:preparadas?|prontas?)\b",
        r"\bservicos? de alimentacao\b", r"\bfornecimento de refeicoes\b",
        r"\bservicos? de buffet\b",
    ),
}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", texto)).strip()


def classificar_categorias_edital(
    objeto: str | None, itens: Sequence[str], *, categorias_ativas: set[str],
) -> list[str]:
    """Reconhece apenas sinais explícitos presentes na taxonomia ativa."""
    textos = [_normalizar(texto) for texto in [objeto or "", *itens] if texto]
    return [
        categoria for categoria, padroes in _PATTERNS.items()
        if categoria in categorias_ativas
        and any(re.search(padrao, texto) for texto in textos for padrao in padroes)
    ]


@dataclass(frozen=True)
class CompanyCategoryClassification:
    categories: tuple[str, ...]
    inferred_from_cnaes: dict[str, tuple[str, ...]]
    unclassified_cnaes: tuple[str, ...]


def classificar_categorias_cnaes(
    profile: CompanyProfile,
    *,
    associations: dict[str, list[TaxonomyAssociation]],
    active_categories: set[str],
    catalog: dict[str, str] | None = None,
) -> CompanyCategoryClassification:
    """Combina categorias explícitas e inferências por CNAE sem usar o edital."""
    categories = set(profile.categories)
    inferred: dict[str, set[str]] = {}
    unclassified: list[str] = []
    descriptions = catalog or {}
    for cnae in profile.cnaes:
        mapped_associations = associations.get(cnae.code, [])
        mapped = {
            item.category for item in mapped_associations
            if item.category in active_categories
            and item.relation_type in {"DIRETA", "RELACIONADA"}
            and item.confidence >= MIN_ASSOCIATION_CONFIDENCE
        }
        if mapped:
            found = mapped
        elif any(item.relation_type == "CONDICIONAL" for item in mapped_associations):
            found = set()
        else:
            description = cnae.description or descriptions.get(cnae.code) or ""
            normalized = _normalizar(description)
            found = {
                category for category, patterns in _PATTERNS.items()
                if category in active_categories
                and any(re.search(pattern, normalized) for pattern in patterns)
            }
        if not found:
            unclassified.append(cnae.code)
            continue
        for category in found:
            inferred.setdefault(category, set()).add(cnae.code)
        categories.update(found)
    return CompanyCategoryClassification(
        categories=tuple(sorted(categories)),
        inferred_from_cnaes={category: tuple(sorted(codes)) for category, codes in sorted(inferred.items())},
        unclassified_cnaes=tuple(unclassified),
    )
