"""Montagem de links canônicos do portal PNCP."""
from __future__ import annotations

import re
from typing import Any


_ID_CONTROLE = re.compile(r"^([A-Za-z0-9]{14})-\d+-(\d{1,})/(\d{4})$")


def url_edital_oficial(
    external_id: str,
    *,
    cnpj: Any = None,
    ano: Any = None,
    sequencial: Any = None,
) -> str | None:
    """Retorna /app/editais/{cnpj}/{ano}/{sequencial:06d} quando identificável."""
    correspondencia = _ID_CONTROLE.fullmatch(external_id.strip())
    cnpj_limpo = re.sub(r"[^A-Za-z0-9]", "", str(cnpj or "")).upper()
    if not re.fullmatch(r"[A-Z0-9]{14}", cnpj_limpo):
        cnpj_limpo = correspondencia.group(1).upper() if correspondencia else ""
    try:
        ano_numero = int(ano) if ano is not None and not isinstance(ano, bool) else None
    except (TypeError, ValueError):
        ano_numero = None
    try:
        sequencial_numero = int(sequencial) if sequencial is not None and not isinstance(sequencial, bool) else None
    except (TypeError, ValueError):
        sequencial_numero = None
    if correspondencia:
        ano_numero = ano_numero or int(correspondencia.group(3))
        sequencial_numero = sequencial_numero or int(correspondencia.group(2))
    if not cnpj_limpo or ano_numero is None or not 1900 <= ano_numero <= 2200 or not sequencial_numero or sequencial_numero < 1:
        return None
    return f"https://pncp.gov.br/app/editais/{cnpj_limpo}/{ano_numero}/{sequencial_numero:06d}"
