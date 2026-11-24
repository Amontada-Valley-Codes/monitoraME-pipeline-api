"""Persistência e chaveamento do cache de respostas das rotas de consulta."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from urllib.parse import parse_qsl, urlencode
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core import orm
from app.core.models import ApiResponseCache


logger = logging.getLogger(__name__)
CACHE_SCHEMA_VERSION = "v1"
MAX_CACHEABLE_REQUEST_BYTES = 1_000_000
MAX_CACHEABLE_RESPONSE_BYTES = 5_000_000
ROUTE_PREFIXES_CACHEAVEIS = ("/pipeline/", "/pncp/", "/public/")


def rota_tem_cache(request: Request) -> bool:
    return (
        request.method in {"GET", "POST"}
        and request.url.path.startswith(ROUTE_PREFIXES_CACHEAVEIS)
    )


def deve_forcar_atualizacao(request: Request) -> bool:
    """Indica que a resposta deve ser recalculada e substituir o cache atual."""
    force_refresh = (
        request.headers.get("x-refresh-force")
        or request.headers.get("x-force-refresh", "")
    ).strip().lower()
    cache_control = {
        diretiva.strip().lower()
        for diretiva in request.headers.get("cache-control", "").split(",")
    }
    return force_refresh in {"1", "true", "yes"} or "no-cache" in cache_control


def _chave_cache(request: Request, corpo: bytes) -> str:
    consulta = urlencode(
        sorted(parse_qsl(request.url.query, keep_blank_values=True)),
        doseq=True,
    )
    corpo_hash = hashlib.sha256(corpo).hexdigest()
    fingerprint = "\n".join(
        (
            CACHE_SCHEMA_VERSION,
            request.method.upper(),
            request.url.path,
            consulta,
            corpo_hash,
        )
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def chave_cache_interna(namespace: str, parametros: dict[str, Any]) -> str:
    """Gera chave estavel para caches analiticos internos por entidade/filtro."""
    fingerprint = json.dumps(
        {
            "schema": CACHE_SCHEMA_VERSION,
            "namespace": namespace,
            "parametros": parametros,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _buscar(cache_key: str) -> dict[str, Any] | None:
    with orm.main_session() as session:
        registro = session.scalar(
            select(ApiResponseCache).where(
                ApiResponseCache.cache_key == cache_key,
            )
        )
        if registro is None:
            return None
        return {
            "status": registro.response_status,
            "content_type": registro.response_content_type,
            "body": registro.response_body,
        }


def buscar_cache_interno(cache_key: str) -> Any | None:
    """Busca um valor JSON armazenado por uma camada interna do pipeline."""
    try:
        registro = _buscar(cache_key)
    except Exception as error:
        logger.warning(
            "Cache interno indisponivel; calculo seguira sem cache",
            extra={"operation": "read", "error_type": type(error).__name__},
        )
        return None
    if registro is None or registro["status"] != 200:
        return None
    return registro["body"]


def _salvar(
    *,
    cache_key: str,
    method: str,
    path: str,
    status: int,
    content_type: str,
    body: Any,
) -> None:
    agora = datetime.now(timezone.utc)
    valores = {
        "cache_key": cache_key,
        "request_method": method,
        "request_path": path,
        "response_status": status,
        "response_content_type": content_type,
        "response_body": body,
        "cached_at": agora,
        "expires_at": None,
    }
    with orm.main_session() as session:
        comando = insert(ApiResponseCache).values(**valores)
        session.execute(
            comando.on_conflict_do_update(
                index_elements=[ApiResponseCache.cache_key],
                set_={
                    "request_method": comando.excluded.request_method,
                    "request_path": comando.excluded.request_path,
                    "response_status": comando.excluded.response_status,
                    "response_content_type": comando.excluded.response_content_type,
                    "response_body": comando.excluded.response_body,
                    "cached_at": comando.excluded.cached_at,
                    "expires_at": None,
                },
            )
        )


def salvar_cache_interno(
    *, cache_key: str, namespace: str, body: Any,
) -> None:
    """Persiste resultado analitico interno no mesmo armazenamento JSON da API."""
    try:
        _salvar(
            cache_key=cache_key,
            method="INTERNAL",
            path=namespace,
            status=200,
            content_type="application/json",
            body=body,
        )
    except Exception as error:
        logger.warning(
            "Nao foi possivel persistir cache interno",
            extra={"operation": "write", "error_type": type(error).__name__},
        )


async def buscar_resposta(request: Request, corpo: bytes) -> dict[str, Any] | None:
    if len(corpo) > MAX_CACHEABLE_REQUEST_BYTES:
        return None
    chave = _chave_cache(request, corpo)
    try:
        return await asyncio.to_thread(_buscar, chave)
    except Exception as error:
        logger.warning(
            "Cache de resposta indisponivel; consulta seguira sem cache",
            extra={"operation": "read", "error_type": type(error).__name__},
        )
        return None


async def salvar_resposta(
    request: Request,
    corpo: bytes,
    *,
    status: int,
    content_type: str,
    resposta: bytes,
) -> None:
    if len(corpo) > MAX_CACHEABLE_REQUEST_BYTES:
        return
    if len(resposta) > MAX_CACHEABLE_RESPONSE_BYTES:
        return
    if "json" not in content_type.lower():
        return
    try:
        payload = json.loads(resposta)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return

    try:
        await asyncio.to_thread(
            _salvar,
            cache_key=_chave_cache(request, corpo),
            method=request.method.upper(),
            path=request.url.path,
            status=status,
            content_type=content_type,
            body=payload,
        )
    except Exception as error:
        logger.warning(
            "Nao foi possivel persistir resposta no cache",
            extra={"operation": "write", "error_type": type(error).__name__},
        )


__all__ = [
    "MAX_CACHEABLE_REQUEST_BYTES",
    "buscar_cache_interno",
    "buscar_resposta",
    "chave_cache_interna",
    "deve_forcar_atualizacao",
    "rota_tem_cache",
    "salvar_cache_interno",
    "salvar_resposta",
]
