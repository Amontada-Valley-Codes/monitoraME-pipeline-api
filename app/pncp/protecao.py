"""Rate limit e circuit breaker PNCP coordenados pelo PostgreSQL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import PncpSourceControl


CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class PoliticaProtecaoPncp:
    requests_per_second: float = 2.0
    concurrency_initial: int = 2
    concurrency_max: int = 5
    circuit_failure_threshold: int = 5
    circuit_cooldown_seconds: float = 60.0
    half_open_max_calls: int = 1
    latency_degrade_seconds: float = 5.0

    @classmethod
    def do_ambiente(cls) -> PoliticaProtecaoPncp:
        politica = cls(
            requests_per_second=float(os.getenv("PNCP_RATE_LIMIT_REQUESTS_PER_SECOND", "2")),
            concurrency_initial=int(os.getenv("PNCP_CONCURRENCY_INITIAL", "2")),
            concurrency_max=int(os.getenv("PNCP_CONCURRENCY_MAX", "5")),
            circuit_failure_threshold=int(os.getenv("PNCP_CIRCUIT_FAILURE_THRESHOLD", "5")),
            circuit_cooldown_seconds=float(os.getenv("PNCP_CIRCUIT_COOLDOWN_SECONDS", "60")),
            half_open_max_calls=int(os.getenv("PNCP_CIRCUIT_HALF_OPEN_MAX_CALLS", "1")),
            latency_degrade_seconds=float(os.getenv("PNCP_LATENCY_DEGRADE_SECONDS", "5")),
        )
        if (
            politica.requests_per_second <= 0 or politica.concurrency_initial < 1
            or politica.concurrency_max < politica.concurrency_initial
            or politica.circuit_failure_threshold < 1 or politica.half_open_max_calls < 1
        ):
            raise ValueError("configuracao de protecao PNCP invalida")
        return politica

    @property
    def interval_milliseconds(self) -> int:
        return max(1, round(1000 / self.requests_per_second))


@dataclass(frozen=True)
class PermissaoFonte:
    permitida: bool
    aguardar: timedelta
    motivo: str | None = None
    estado_circuito: str = CLOSED


class PncpProtegidoError(RuntimeError):
    """Chamada adiada antes de HTTP; não deve consumir tentativa do job."""

    reagendar_sem_tentativa = True
    retryable = True

    def __init__(self, permissao: PermissaoFonte) -> None:
        super().__init__(permissao.motivo or "fonte PNCP temporariamente protegida")
        self.retry_after = permissao.aguardar
        self.permissao = permissao

    def contexto(self) -> dict[str, object]:
        return {
            "categoria": "protecao_fonte",
            "motivo": self.permissao.motivo,
            "circuit_state": self.permissao.estado_circuito,
            "retryable": True,
        }


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _controle_session(session: Session, *, source: str, politica: PoliticaProtecaoPncp) -> PncpSourceControl:
    # O upsert elimina a corrida de inicialização quando vários workers sobem juntos.
    session.execute(
        insert(PncpSourceControl)
        .values(
            source=source, concurrency_limit=politica.concurrency_initial,
            interval_milliseconds=politica.interval_milliseconds,
        )
        .on_conflict_do_nothing(index_elements=[PncpSourceControl.source])
    )
    return session.execute(
        select(PncpSourceControl).where(PncpSourceControl.source == source).with_for_update()
    ).scalar_one()


def adquirir_permissao_session(
    session: Session, *, source: str, politica: PoliticaProtecaoPncp, agora: datetime | None = None
) -> PermissaoFonte:
    """Reserva de forma serializada um slot global, sem manter transação no HTTP."""
    agora = agora or _agora()
    controle = _controle_session(session, source=source, politica=politica)
    if controle.circuit_state == OPEN:
        if controle.open_until is not None and controle.open_until > agora:
            return PermissaoFonte(False, controle.open_until - agora, "circuito aberto", OPEN)
        controle.circuit_state, controle.half_open_in_flight = HALF_OPEN, 0
    if controle.circuit_state == HALF_OPEN and controle.half_open_in_flight >= politica.half_open_max_calls:
        return PermissaoFonte(False, timedelta(seconds=1), "sonda half-open em andamento", HALF_OPEN)
    if controle.active_requests >= controle.concurrency_limit:
        return PermissaoFonte(False, timedelta(seconds=1), "limite de concorrencia", controle.circuit_state)
    slot = max(agora, controle.next_request_at or agora)
    if slot > agora:
        controle.next_request_at = slot + timedelta(milliseconds=controle.interval_milliseconds)
        return PermissaoFonte(False, slot - agora, "rate limit global", controle.circuit_state)
    controle.next_request_at = agora + timedelta(milliseconds=controle.interval_milliseconds)
    controle.active_requests += 1
    if controle.circuit_state == HALF_OPEN:
        controle.half_open_in_flight += 1
    controle.updated_at = agora
    return PermissaoFonte(True, timedelta(), estado_circuito=controle.circuit_state)


def registrar_resultado_session(
    session: Session,
    *,
    source: str,
    sucesso: bool,
    retryable: bool,
    status_code: int | None,
    latency_seconds: float,
    politica: PoliticaProtecaoPncp,
    agora: datetime | None = None,
) -> None:
    agora = agora or _agora()
    controle = _controle_session(session, source=source, politica=politica)
    controle.active_requests = max(0, controle.active_requests - 1)
    if controle.circuit_state == HALF_OPEN:
        controle.half_open_in_flight = max(0, controle.half_open_in_flight - 1)
    if sucesso:
        controle.consecutive_failures = 0
        if controle.circuit_state == HALF_OPEN:
            controle.circuit_state, controle.open_until, controle.last_reason = CLOSED, None, "recuperado por sonda"
        if latency_seconds >= politica.latency_degrade_seconds:
            controle.concurrency_limit = max(1, controle.concurrency_limit - 1)
            controle.successes_since_reduction = 0
            controle.last_reason = "latencia elevada"
        else:
            controle.successes_since_reduction += 1
            if controle.successes_since_reduction >= 5 and controle.concurrency_limit < politica.concurrency_max:
                controle.concurrency_limit += 1
                controle.successes_since_reduction = 0
            if controle.interval_milliseconds > politica.interval_milliseconds:
                controle.interval_milliseconds = max(
                    politica.interval_milliseconds,
                    controle.interval_milliseconds - max(1, politica.interval_milliseconds // 4),
                )
        controle.updated_at = agora
        return
    if not retryable:
        controle.updated_at = agora
        return
    controle.consecutive_failures += 1
    controle.last_reason = f"falha transitoria HTTP {status_code}" if status_code else "falha transitoria"
    if status_code == 429 or latency_seconds >= politica.latency_degrade_seconds:
        controle.concurrency_limit = max(1, controle.concurrency_limit - 1)
        controle.successes_since_reduction = 0
    if status_code == 429:
        controle.interval_milliseconds = min(60_000, controle.interval_milliseconds * 2)
    if controle.circuit_state == HALF_OPEN or controle.consecutive_failures >= politica.circuit_failure_threshold:
        controle.circuit_state = OPEN
        controle.open_until = agora + timedelta(seconds=politica.circuit_cooldown_seconds)
        controle.last_reason = "circuito aberto apos falhas transitorias"
    controle.updated_at = agora


class ProtecaoPncp:
    def __init__(self, *, source: str = "PNCP", politica: PoliticaProtecaoPncp | None = None) -> None:
        self.source = source
        self.politica = politica or PoliticaProtecaoPncp.do_ambiente()

    def adquirir(self) -> PermissaoFonte:
        with orm.main_session() as session:
            return adquirir_permissao_session(session, source=self.source, politica=self.politica)

    def registrar_resultado(self, *, sucesso: bool, retryable: bool = False, status_code: int | None = None, latency_seconds: float = 0) -> None:
        with orm.main_session() as session:
            registrar_resultado_session(
                session, source=self.source, sucesso=sucesso, retryable=retryable,
                status_code=status_code, latency_seconds=latency_seconds, politica=self.politica,
            )

    def metricas(self) -> dict[str, object]:
        with orm.main_session() as session:
            controle = session.get(PncpSourceControl, self.source)
            if controle is None:
                return {"source": self.source, "circuit_state": CLOSED, "last_reason": None}
            return {
                "source": controle.source, "circuit_state": controle.circuit_state,
                "consecutive_failures": controle.consecutive_failures,
                "concurrency_limit": controle.concurrency_limit,
                "active_requests": controle.active_requests,
                "last_reason": controle.last_reason, "open_until": controle.open_until,
            }
