from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATABASE_URL, LOG_DATABASE_URL


DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "require")
LOG_DATABASE_SSLMODE = os.getenv("LOG_DATABASE_SSLMODE") or os.getenv("DATABASE_SSLMODE", "require")


class MainBase(DeclarativeBase):
    pass


class LogBase(DeclarativeBase):
    pass


_main_engine: Engine | None = None
_log_engine: Engine | None = None
_main_session_factory: sessionmaker[Session] | None = None
_log_session_factory: sessionmaker[Session] | None = None
_medicao_banco_atual: ContextVar[dict[str, float | int] | None] = ContextVar(
    "medicao_banco_atual",
    default=None,
)


def iniciar_medicao_banco() -> Any:
    """Inicia uma medicao opt-in das consultas no banco principal.

    O dicionario e intencionalmente mutavel: o contexto da requisicao e copiado
    pelo FastAPI para a thread que executa o endpoint sincrono, mas ambas as
    threads continuam apontando para o mesmo acumulador.
    """
    return _medicao_banco_atual.set({"duration_ms": 0.0, "queries": 0})


def obter_medicao_banco() -> dict[str, float | int]:
    medicao = _medicao_banco_atual.get()
    if medicao is None:
        return {"duration_ms": 0.0, "queries": 0}
    return dict(medicao)


def finalizar_medicao_banco(token: Any) -> None:
    _medicao_banco_atual.reset(token)


def _iniciar_consulta_banco(
    _conn: Any,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    context: Any,
    _executemany: bool,
) -> None:
    if _medicao_banco_atual.get() is not None:
        context._monitorame_inicio_consulta = perf_counter()


def _concluir_consulta_banco(context: Any) -> None:
    inicio = getattr(context, "_monitorame_inicio_consulta", None)
    if inicio is None:
        return
    context._monitorame_inicio_consulta = None
    medicao = _medicao_banco_atual.get()
    if medicao is None:
        return
    medicao["duration_ms"] = float(medicao["duration_ms"]) + (
        perf_counter() - inicio
    ) * 1000
    medicao["queries"] = int(medicao["queries"]) + 1


def _finalizar_consulta_banco(
    _conn: Any,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    context: Any,
    _executemany: bool,
) -> None:
    _concluir_consulta_banco(context)


def _finalizar_consulta_banco_com_erro(exception_context: Any) -> None:
    context = exception_context.execution_context
    if context is not None:
        _concluir_consulta_banco(context)


def _registrar_medicao_banco(engine: Engine) -> None:
    event.listen(engine, "before_cursor_execute", _iniciar_consulta_banco)
    event.listen(engine, "after_cursor_execute", _finalizar_consulta_banco)
    event.listen(engine, "handle_error", _finalizar_consulta_banco_com_erro)


def _psycopg_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def main_database_url() -> str:
    database_url = os.getenv("DATABASE_URL") or DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("DATABASE_URL nao esta definida")
    return _psycopg_database_url(database_url.strip())


def log_database_url() -> str:
    database_url = os.getenv("LOG_DATABASE_URL") or LOG_DATABASE_URL
    if not database_url.strip():
        raise RuntimeError("LOG_DATABASE_URL nao esta definida")
    return _psycopg_database_url(database_url.strip())


def main_connect_args() -> dict[str, str]:
    return _connect_args(main_database_url(), os.getenv("DATABASE_SSLMODE") or DATABASE_SSLMODE or "require")


def log_connect_args() -> dict[str, str]:
    sslmode = os.getenv("LOG_DATABASE_SSLMODE") or LOG_DATABASE_SSLMODE or "require"
    return _connect_args(log_database_url(), sslmode)


def _connect_args(database_url: str, sslmode: str) -> dict[str, str]:
    if database_url.startswith("postgresql"):
        return {"sslmode": sslmode.strip()}
    return {}


def _engine(database_url: str, connect_args: dict[str, str]) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=0,
        connect_args=connect_args,
    )


def get_main_engine() -> Engine:
    global _main_engine

    if _main_engine is None:
        _main_engine = _engine(main_database_url(), main_connect_args())
        _registrar_medicao_banco(_main_engine)
    return _main_engine


def get_log_engine() -> Engine:
    global _log_engine

    if _log_engine is None:
        _log_engine = _engine(log_database_url(), log_connect_args())
    return _log_engine


def get_main_session_factory() -> sessionmaker[Session]:
    global _main_session_factory

    if _main_session_factory is None:
        _main_session_factory = sessionmaker(
            bind=get_main_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _main_session_factory


def get_log_session_factory() -> sessionmaker[Session]:
    global _log_session_factory

    if _log_session_factory is None:
        _log_session_factory = sessionmaker(
            bind=get_log_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _log_session_factory


def new_main_session() -> Session:
    return get_main_session_factory()()


def new_log_session() -> Session:
    return get_log_session_factory()()


@contextmanager
def main_session() -> Iterator[Session]:
    session = new_main_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def log_session() -> Iterator[Session]:
    session = new_log_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engines() -> None:
    global _main_engine, _log_engine, _main_session_factory, _log_session_factory

    if _main_engine is not None:
        _main_engine.dispose()
    if _log_engine is not None:
        _log_engine.dispose()
    _main_engine = None
    _log_engine = None
    _main_session_factory = None
    _log_session_factory = None


def dispose_main_engine() -> None:
    global _main_engine, _main_session_factory

    if _main_engine is not None:
        _main_engine.dispose()
    _main_engine = None
    _main_session_factory = None


def dispose_log_engine() -> None:
    global _log_engine, _log_session_factory

    if _log_engine is not None:
        _log_engine.dispose()
    _log_engine = None
    _log_session_factory = None


__all__ = [
    "LogBase",
    "MainBase",
    "dispose_engines",
    "dispose_log_engine",
    "dispose_main_engine",
    "finalizar_medicao_banco",
    "get_log_engine",
    "get_log_session_factory",
    "get_main_engine",
    "get_main_session_factory",
    "iniciar_medicao_banco",
    "log_connect_args",
    "log_database_url",
    "log_session",
    "main_connect_args",
    "main_database_url",
    "main_session",
    "new_log_session",
    "new_main_session",
    "obter_medicao_banco",
]
