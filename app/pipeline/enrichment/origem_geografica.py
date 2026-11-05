from __future__ import annotations

import unicodedata

NIVEL_MESMO_MUNICIPIO = "Sediado no município comprador"
NIVEL_OUTRO_MUNICIPIO_CE = "Outro município do Ceará"
NIVEL_FORA_DO_ESTADO = "Fora do estado"


def normalizar_texto(texto: str | None) -> str:
    """Remove acentos e padroniza caixa/espacos de textos comparaveis."""
    texto = str(texto or "").strip().upper()
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def classificar_origem_geografica(
    municipio_comprador: str,
    uf_fornecedor: str,
    municipio_fornecedor: str,
    uf_comprador: str = "CE",
) -> str:
    """
    Classifica a origem do fornecedor em relacao ao municipio comprador.
    """
    uf_forn, uf_comp = normalizar_texto(uf_fornecedor), normalizar_texto(uf_comprador)
    if uf_forn != uf_comp:
        return NIVEL_FORA_DO_ESTADO
    mesmo_municipio = normalizar_texto(municipio_fornecedor) == normalizar_texto(municipio_comprador)
    return NIVEL_MESMO_MUNICIPIO if mesmo_municipio else NIVEL_OUTRO_MUNICIPIO_CE


__all__ = [
    "NIVEL_FORA_DO_ESTADO",
    "NIVEL_MESMO_MUNICIPIO",
    "NIVEL_OUTRO_MUNICIPIO_CE",
    "classificar_origem_geografica",
]
