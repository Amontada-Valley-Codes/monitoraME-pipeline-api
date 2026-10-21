"""Politica unica de classificacao e agendamento de retries do PNCP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
import random
from typing import Callable


HTTP_TRANSITORIOS = frozenset({408, 429, 500, 502, 503, 504})
HTTP_PERMANENTES = frozenset({400, 401, 403})


@dataclass(frozen=True)
class PoliticaRetryPncp:
    """Configuracao de retry; ``max_attempts`` inclui a primeira chamada."""

    max_attempts: int = 5
    initial_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0

    @classmethod
    def do_ambiente(cls) -> PoliticaRetryPncp:
        politica = cls(
            max_attempts=int(os.getenv("PNCP_MAX_ATTEMPTS", "5")),
            initial_delay_seconds=float(os.getenv("PNCP_RETRY_INITIAL_SECONDS", "2")),
            max_delay_seconds=float(os.getenv("PNCP_RETRY_MAX_SECONDS", "60")),
        )
        if politica.max_attempts < 1:
            raise ValueError("PNCP_MAX_ATTEMPTS deve ser maior que zero")
        if politica.initial_delay_seconds < 0 or politica.max_delay_seconds < politica.initial_delay_seconds:
            raise ValueError("intervalos de retry PNCP invalidos")
        return politica

    def atraso_com_jitter(
        self, *, attempts: int, aleatorio: Callable[[float, float], float] = random.uniform
    ) -> timedelta:
        """Full jitter em ``[0, min(teto, inicial * 2 ** (n - 1))]``."""
        expoente = max(0, attempts - 1)
        teto = min(self.max_delay_seconds, self.initial_delay_seconds * (2**expoente))
        return timedelta(seconds=aleatorio(0.0, teto))


@dataclass(frozen=True)
class DecisaoRetry:
    repetir: bool
    atraso: timedelta | None
    motivo: str


def retry_after_para_timedelta(valor: str | None, *, agora: datetime | None = None) -> timedelta | None:
    """Converte Retry-After em segundos ou data HTTP, sem reduzir o prazo indicado."""
    if not valor:
        return None
    try:
        return timedelta(seconds=max(0.0, float(valor)))
    except ValueError:
        try:
            data = parsedate_to_datetime(valor)
        except (TypeError, ValueError):
            return None
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        referencia = agora or datetime.now(timezone.utc)
        return max(data - referencia, timedelta())


def classificar_status_http(status_code: int | None) -> bool:
    """Retorna se o status pode ser repetido automaticamente."""
    return status_code in HTTP_TRANSITORIOS if status_code is not None else True


def decidir_retry(
    *,
    attempts: int,
    max_attempts: int,
    retryable: bool,
    retry_after: timedelta | None = None,
    politica: PoliticaRetryPncp | None = None,
) -> DecisaoRetry:
    if not retryable:
        return DecisaoRetry(False, None, "falha permanente")
    if attempts >= max_attempts:
        return DecisaoRetry(False, None, "tentativas esgotadas")
    if retry_after is not None:
        return DecisaoRetry(True, retry_after, "Retry-After")
    politica = politica or PoliticaRetryPncp.do_ambiente()
    return DecisaoRetry(True, politica.atraso_com_jitter(attempts=attempts), "backoff com full jitter")
