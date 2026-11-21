"""Classificação conservadora do objeto PNCP na taxonomia local."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence


CATEGORY_RULE_VERSION = "pncp-object-categories-v1"

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
