from __future__ import annotations

from enum import StrEnum


class CompanySize(StrEnum):
    ME = "ME"
    MEI = "MEI"
    EPP = "EPP"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SupplierOrigin(StrEnum):
    NO_MUNICIPIO_COMPRADOR = "NO_MUNICIPIO_COMPRADOR"
    EM_OUTRO_MUNICIPIO = "EM_OUTRO_MUNICIPIO"
    FORA_DO_CEARA = "FORA_DO_CEARA"
    ORIGEM_NAO_IDENTIFICADA = "ORIGEM_NAO_IDENTIFICADA"


__all__ = ["CompanySize", "SupplierOrigin"]
