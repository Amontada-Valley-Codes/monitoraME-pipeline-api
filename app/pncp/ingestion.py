"""Coleta incremental da listagem publica de contratacoes do PNCP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

from app.core import orm
from app.pncp.jobs import JobReivindicado, NovoJob, PNCP_QUERY_CONFIG_VERSION
from app.pncp.protecao import PncpProtegidoError, ProtecaoPncp
from app.pncp.retry import classificar_status_http, retry_after_para_timedelta
from app.pncp.raw import (
    RegistroRawPncp,
    identificar_contratacao_raw,
    salvar_raw_e_agendar_normalizacao_session,
)
from app.pncp.checkpoints import ChaveCursor, confirmar_janela_job


ENDPOINT_PUBLICACAO = "/v1/contratacoes/publicacao"
ENDPOINT_ATUALIZACAO = "/v1/contratacoes/atualizacao"
ENDPOINT_PROPOSTAS_ABERTAS = "/v1/contratacoes/proposta"
FONTE = "PNCP"
TIPO = "CONTRATACAO"
MODALIDADE_PREGAO_ELETRONICO = 6
MODALIDADES_PNCP_PADRAO = tuple(range(1, 15))
UFS_NORDESTE = ("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE")
logger = logging.getLogger(__name__)


class PncpColetaError(RuntimeError):
    """Erro de coleta com dados suficientes para o job decidir o retry."""

    def __init__(
        self,
        mensagem: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
        retry_after: timedelta | None = None,
        endpoint: str | None = None,
        page: int | None = None,
        diagnostico: dict[str, object] | None = None,
    ) -> None:
        super().__init__(mensagem)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.endpoint = endpoint
        self.page = page
        self.diagnostico = diagnostico or {}

    def contexto(self) -> dict[str, object]:
        return {
            "categoria": "pncp_coleta",
            "endpoint": self.endpoint,
            "page": self.page,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after.total_seconds() if self.retry_after else None,
            **self.diagnostico,
        }


@dataclass(frozen=True)
class PaginaContratacoes:
    itens: list[dict[str, Any]]
    total_paginas: int | None


def data_atualizacao_fonte(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        atualizado = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return atualizado if atualizado.tzinfo is not None else None


class ClientePncp:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: tuple[float, float] | None = None,
        session: requests.Session | None = None,
        protecao: ProtecaoPncp | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta")).rstrip("/")
        self.timeout = timeout or (float(os.getenv("PNCP_CONNECT_TIMEOUT_SECONDS", "10")), float(os.getenv("PNCP_READ_TIMEOUT_SECONDS", "30")))
        self.request_interval_seconds = float(os.getenv("PNCP_REQUEST_INTERVAL_SECONDS", "2"))
        self._ultima_requisicao_monotonic: float | None = None
        self.session = session or requests.Session()
        self.protecao = protecao or ProtecaoPncp()

    def buscar_publicacoes(
        self,
        *,
        inicio: date,
        fim: date,
        modalidade: int,
        pagina: int,
        tamanho_pagina: int,
        uf: str | None = None,
    ) -> PaginaContratacoes:
        parametros: dict[str, str | int] = {
            "dataInicial": inicio.strftime("%Y%m%d"),
            "dataFinal": fim.strftime("%Y%m%d"),
        }
        parametros["codigoModalidadeContratacao"] = modalidade
        if uf is not None:
            parametros["uf"] = uf
        return self._buscar(
            ENDPOINT_PUBLICACAO,
            parametros=parametros,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )

    def buscar_atualizacoes(
        self,
        *,
        inicio: date,
        fim: date,
        modalidade: int,
        pagina: int,
        tamanho_pagina: int,
        uf: str | None = None,
    ) -> PaginaContratacoes:
        parametros: dict[str, str | int] = {
            "dataInicial": inicio.strftime("%Y%m%d"),
            "dataFinal": fim.strftime("%Y%m%d"),
        }
        parametros["codigoModalidadeContratacao"] = modalidade
        if uf is not None:
            parametros["uf"] = uf
        return self._buscar(
            ENDPOINT_ATUALIZACAO,
            parametros=parametros,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )

    def buscar_propostas_abertas(
        self,
        *,
        data_final: date,
        pagina: int,
        tamanho_pagina: int,
        modalidade: int | None = None,
        uf: str | None = None,
    ) -> PaginaContratacoes:
        parametros: dict[str, Any] = {"dataFinal": data_final.strftime("%Y%m%d")}
        if modalidade is not None:
            parametros["codigoModalidadeContratacao"] = modalidade
        if uf is not None:
            if uf not in UFS_NORDESTE:
                raise ValueError("uf deve ser uma UF do Nordeste suportada")
            parametros["uf"] = uf
        return self._buscar(ENDPOINT_PROPOSTAS_ABERTAS, parametros=parametros, pagina=pagina, tamanho_pagina=tamanho_pagina)

    def _adquirir_permissao(self) -> None:
        limite_espera = time.monotonic() + 90
        while True:
            permissao = self.protecao.adquirir()
            if permissao.permitida:
                return
            if (
                permissao.motivo not in {"rate limit global", "limite de concorrencia"}
                or time.monotonic() + permissao.aguardar.total_seconds() > limite_espera
            ):
                raise PncpProtegidoError(permissao)
            time.sleep(max(0.01, permissao.aguardar.total_seconds()))

    def _buscar(self, endpoint: str, *, parametros: dict[str, Any], pagina: int, tamanho_pagina: int) -> PaginaContratacoes:
        if pagina < 1 or tamanho_pagina < 1 or tamanho_pagina > 50:
            raise ValueError("pagina deve ser positiva e tamanho_pagina deve estar entre 1 e 50")
        params = {**parametros, "pagina": pagina, "tamanhoPagina": tamanho_pagina}
        if self._ultima_requisicao_monotonic is not None:
            intervalo = time.monotonic() - self._ultima_requisicao_monotonic
            if intervalo < self.request_interval_seconds:
                time.sleep(self.request_interval_seconds - intervalo)
        self._adquirir_permissao()
        self._ultima_requisicao_monotonic = time.monotonic()
        inicio_chamada = time.monotonic()
        contexto_requisicao: dict[str, object] = {
            "query": params,
            "timeout_connect_seconds": self.timeout[0],
            "timeout_read_seconds": self.timeout[1],
        }
        try:
            resposta = self.session.get(f"{self.base_url}{endpoint}", params=params, timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.Timeout as exc:
            contexto_requisicao.update({
                "transport_error": type(exc).__name__,
                "duration_ms": round((time.monotonic() - inicio_chamada) * 1000, 2),
            })
            self.protecao.registrar_resultado(sucesso=False, retryable=True, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError(
                f"falha na pagina {pagina}: timeout", endpoint=endpoint, page=pagina,
                diagnostico=contexto_requisicao,
            ) from exc
        except requests.RequestException as exc:
            contexto_requisicao.update({
                "transport_error": type(exc).__name__,
                "duration_ms": round((time.monotonic() - inicio_chamada) * 1000, 2),
            })
            self.protecao.registrar_resultado(sucesso=False, retryable=True, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError(
                f"falha na pagina {pagina}: {type(exc).__name__}", endpoint=endpoint,
                page=pagina, diagnostico=contexto_requisicao,
            ) from exc
        status = resposta.status_code
        cabecalhos = getattr(resposta, "headers", {}) or {}
        cabecalhos_lower = {str(nome).lower(): valor for nome, valor in cabecalhos.items()}
        request_id = next((
            _valor_cabecalho(cabecalhos_lower[nome])
            for nome in ("x-request-id", "x-correlation-id", "traceparent")
            if cabecalhos_lower.get(nome)
        ), None)
        content_type = cabecalhos_lower.get("content-type")
        content_length = cabecalhos_lower.get("content-length")
        try:
            corpo_bytes = _bytes_resposta(resposta)
        except requests.RequestException as exc:
            contexto_corpo = {
                **contexto_requisicao,
                "status_code": status,
                "duration_ms": round((time.monotonic() - inicio_chamada) * 1000, 2),
                "content_type": _valor_cabecalho(content_type),
                "content_length_header": _valor_cabecalho(content_length, limite=40),
                "upstream_request_id": request_id,
                "response_body_error": type(exc).__name__,
            }
            self.protecao.registrar_resultado(
                sucesso=False, retryable=True, status_code=status,
                latency_seconds=time.monotonic() - inicio_chamada,
            )
            raise PncpColetaError(
                f"falha ao decodificar corpo da pagina {pagina}",
                endpoint=endpoint, page=pagina, status_code=status,
                diagnostico=contexto_corpo,
            ) from exc
        contexto_resposta = {
            **contexto_requisicao,
            "status_code": status,
            "duration_ms": round((time.monotonic() - inicio_chamada) * 1000, 2),
            "content_type": _valor_cabecalho(content_type),
            "content_length_header": _valor_cabecalho(content_length, limite=40),
            "response_size_bytes": len(corpo_bytes),
            "response_sha256": hashlib.sha256(corpo_bytes).hexdigest(),
            "upstream_request_id": request_id,
        }
        if status >= 400:
            retryable = classificar_status_http(status)
            self.protecao.registrar_resultado(
                sucesso=False, retryable=retryable, status_code=status,
                latency_seconds=time.monotonic() - inicio_chamada,
            )
            raise PncpColetaError(
                f"falha na pagina {pagina}: HTTP {status}",
                retryable=retryable,
                status_code=status,
                retry_after=retry_after_para_timedelta(resposta.headers.get("Retry-After")) if status == 429 else None,
                endpoint=endpoint,
                page=pagina,
                diagnostico=contexto_resposta,
            )
        if status == 204:
            # O PNCP usa 204 para uma consulta sem resultados. Não há corpo
            # JSON para decodificar; total_paginas=0 encerra a janela vazia.
            self.protecao.registrar_resultado(
                sucesso=True, status_code=status,
                latency_seconds=time.monotonic() - inicio_chamada,
            )
            return PaginaContratacoes([], 0)
        try:
            corpo = resposta.json()
        except ValueError as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            if isinstance(exc, json.JSONDecodeError):
                contexto_resposta.update({
                    "json_error": exc.msg[:200],
                    "json_error_position": exc.pos,
                    "json_error_line": exc.lineno,
                    "json_error_column": exc.colno,
                })
            else:
                contexto_resposta["json_error"] = type(exc).__name__
            raise PncpColetaError(
                f"falha na pagina {pagina}: JSON invalido", endpoint=endpoint,
                page=pagina, status_code=status, diagnostico=contexto_resposta,
            ) from exc
        if not isinstance(corpo, dict):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("resposta PNCP nao e objeto JSON", endpoint=endpoint, page=pagina, status_code=status, diagnostico=contexto_resposta)
        itens = corpo.get("data", corpo.get("items"))
        if not isinstance(itens, list) or any(not isinstance(item, dict) for item in itens):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("resposta PNCP sem lista valida de contratacoes", endpoint=endpoint, page=pagina, status_code=status, diagnostico=contexto_resposta)
        total = corpo.get("totalPaginas", corpo.get("totalPages"))
        if total is not None and (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or (total == 0 and itens)
            or (pagina > total and itens)
        ):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("metadado de paginacao PNCP invalido", endpoint=endpoint, page=pagina, status_code=status, diagnostico=contexto_resposta)
        self.protecao.registrar_resultado(sucesso=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
        return PaginaContratacoes(itens, total)


def _bytes_resposta(resposta: requests.Response) -> bytes:
    """Obtém bytes já recebidos sem propagar o conteúdo para logs ou erros."""
    conteudo = getattr(resposta, "content", b"")
    if isinstance(conteudo, bytes):
        return conteudo
    if isinstance(conteudo, bytearray):
        return bytes(conteudo)
    texto = getattr(resposta, "text", "")
    return texto.encode("utf-8", errors="replace") if isinstance(texto, str) else b""


def _valor_cabecalho(valor: object, *, limite: int = 200) -> str | None:
    if not isinstance(valor, (str, int, float)):
        return None
    return str(valor).replace("\r", "").replace("\n", "")[:limite] or None


def agendar_publicacao(
    *,
    inicio: datetime,
    fim: datetime,
    modalidades: list[int] | None = None,
    ufs: tuple[str, ...] | list[str] | None = None,
    tamanho_pagina: int = 50,
    prioridade: int = 70,
) -> list[NovoJob]:
    if inicio.tzinfo is None or fim.tzinfo is None or fim < inicio:
        raise ValueError("janela com timezone e ordem valida e obrigatoria")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    jobs: list[NovoJob] = []
    if ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)):
        raise ValueError("ufs deve conter somente UFs do Nordeste")
    codigos_modalidade = modalidades or [MODALIDADE_PREGAO_ELETRONICO]
    for uf in (ufs or (None,)):
        for modalidade in codigos_modalidade:
            uf_scope = f":uf:{uf}" if uf is not None else ""
            scope = (
                f"publicacao:{inicio.date().isoformat()}:{fim.date().isoformat()}"
                f"{uf_scope}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
            )
            dedupe_key = f"{scope}:config:{PNCP_QUERY_CONFIG_VERSION}"
            jobs.append(NovoJob(
                "incremental",
                TIPO,
                FONTE,
                scope,
                dedupe_key,
                prioridade,
                datetime.now(timezone.utc),
                window_start=inicio,
                window_end=fim,
                page=1,
                parameters={
                    "flow": "publicacao",
                    "codigoModalidadeContratacao": modalidade,
                    "uf": uf,
                    "tamanhoPagina": tamanho_pagina,
                },
                config_version=PNCP_QUERY_CONFIG_VERSION,
            ))
    return jobs


def agendar_backfill_anual(
    *,
    ano: int,
    modalidades: list[int] | None = None,
    ufs: tuple[str, ...] | list[str] | None = None,
    agora: datetime | None = None,
    tamanho_janela_dias: int = 7,
    tamanho_pagina: int = 50,
) -> list[NovoJob]:
    """Agenda o ano por janelas curtas para limitar páginas por execução."""
    if (
        ano < 2000
        or tamanho_janela_dias < 1
        or (ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)))
        or not 1 <= tamanho_pagina <= 50
    ):
        raise ValueError("ano, janela e tamanho de página inválidos")
    atual = agora or datetime.now(timezone.utc)
    if atual.tzinfo is None:
        raise ValueError("agora deve incluir timezone")
    inicio_ano = datetime(ano, 1, 1, tzinfo=atual.tzinfo)
    fim_ano = min(datetime(ano + 1, 1, 1, tzinfo=atual.tzinfo), atual)
    if fim_ano <= inicio_ano:
        return []
    jobs: list[NovoJob] = []
    codigos_modalidade = modalidades or [MODALIDADE_PREGAO_ELETRONICO]
    inicio = inicio_ano
    while inicio < fim_ano:
        fim = min(inicio + timedelta(days=tamanho_janela_dias) - timedelta(seconds=1), fim_ano)
        for uf in (ufs or (None,)):
            for modalidade in codigos_modalidade:
                uf_scope = f":uf:{uf}" if uf is not None else ""
                scope = (
                    f"backfill:{ano}:publicacao:{inicio.date().isoformat()}:{fim.date().isoformat()}"
                    f"{uf_scope}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
                )
                dedupe_key = f"{scope}:config:{PNCP_QUERY_CONFIG_VERSION}"
                jobs.append(NovoJob("backfill", TIPO, FONTE, scope, dedupe_key, 20, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1, parameters={"flow": "publicacao", "codigoModalidadeContratacao": modalidade, "uf": uf, "tamanhoPagina": tamanho_pagina}, config_version=PNCP_QUERY_CONFIG_VERSION))
        inicio = fim + timedelta(seconds=1)
    return jobs


def agendar_propostas_abertas(
    *,
    data_final: datetime,
    modalidades: list[int] | None = None,
    ufs: tuple[str, ...] | list[str] | None = ("CE",),
    tamanho_pagina: int = 50,
    prioridade: int = 100,
) -> list[NovoJob]:
    if data_final.tzinfo is None:
        raise ValueError("data_final deve incluir timezone")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    if ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)):
        raise ValueError("ufs deve conter somente UFs do Nordeste")
    codigos = modalidades or list(MODALIDADES_PNCP_PADRAO)
    jobs: list[NovoJob] = []
    for uf in (ufs or (None,)):
        for modalidade in codigos:
            codigo = str(modalidade)
            uf_scope = f":uf:{uf}" if uf is not None else ""
            scope = f"propostas-abertas:{data_final.date().isoformat()}{uf_scope}:modalidade:{codigo}:tamanho:{tamanho_pagina}"
            dedupe_key = f"{scope}:config:{PNCP_QUERY_CONFIG_VERSION}"
            jobs.append(NovoJob(
                "incremental", TIPO, FONTE, scope, dedupe_key, prioridade,
                datetime.now(timezone.utc), window_end=data_final, page=1,
                parameters={
                    "flow": "propostas_abertas",
                    "dataFinal": data_final.date().isoformat(),
                    "codigoModalidadeContratacao": modalidade,
                    "uf": uf,
                    "tamanhoPagina": tamanho_pagina,
                },
                config_version=PNCP_QUERY_CONFIG_VERSION,
            ))
    return jobs


def coletar_propostas_abertas(
    job: JobReivindicado,
    cliente: ClientePncp,
    *,
    tamanho_pagina: int = 50,
) -> int:
    """Coleta somente contratações que ainda recebem propostas, sem cursor histórico."""
    if (
        job.source != FONTE
        or job.entity_type != TIPO
        or job.job_type != "incremental"
        or not job.scope_key.startswith("propostas-abertas:")
    ):
        raise ValueError("job nao representa uma consulta PNCP de propostas abertas")
    tamanho_pagina = tamanho_pagina_do_job(job, tamanho_pagina)
    try:
        modalidade = int(job.parameters["codigoModalidadeContratacao"])
        data_final = date.fromisoformat(str(job.parameters["dataFinal"]))
        uf = job.parameters.get("uf")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("parametros do job de propostas abertas invalidos") from exc
    if not isinstance(uf, str) or uf not in UFS_NORDESTE:
        raise ValueError("job de propostas abertas exige uma UF suportada")
    pagina = job.page if job.page is not None else 1
    if isinstance(pagina, bool) or pagina != 1:
        raise ValueError("consulta de propostas abertas deve reiniciar na pagina 1")

    total_registros = 0
    paginas_lidas = 0
    raw_criados = 0
    raw_reutilizados = 0
    normalizacoes_criadas = 0
    normalizacoes_reutilizadas = 0
    while True:
        inicio_pagina = time.monotonic()
        resultado = cliente.buscar_propostas_abertas(
            data_final=data_final,
            modalidade=modalidade,
            uf=uf,
            pagina=pagina,
            tamanho_pagina=tamanho_pagina,
        )
        for item in resultado.itens:
            external_id, _ = identificar_contratacao_raw(item)
            with orm.main_session() as session:
                persistencia = salvar_raw_e_agendar_normalizacao_session(
                    session,
                    RegistroRawPncp(
                        entity_type=TIPO,
                        external_id=external_id,
                        payload=item,
                        collected_at=datetime.now(timezone.utc),
                        source_updated_at=data_atualizacao_fonte(item.get("dataAtualizacao")),
                        run_id=f"job:{job.id}:pagina:{pagina}",
                    ),
                )
            total_registros += 1
            raw_criados += int(persistencia.raw_criado)
            raw_reutilizados += int(not persistencia.raw_criado)
            normalizacoes_criadas += int(persistencia.job_criado)
            normalizacoes_reutilizadas += int(not persistencia.job_criado)

        paginas_lidas += 1
        logger.info(
            "Pagina PNCP de propostas abertas processada",
            extra={
                "job_id": job.id,
                "flow": "propostas_abertas",
                "uf": uf,
                "modalidade": modalidade,
                "data_final": data_final.isoformat(),
                "pagina": pagina,
                "paginas_totais": resultado.total_paginas,
                "registros_pagina": len(resultado.itens),
                "registros_acumulados": total_registros,
                "duracao_pagina_segundos": round(time.monotonic() - inicio_pagina, 3),
            },
        )
        if resultado.total_paginas is not None:
            if not resultado.itens and pagina < resultado.total_paginas:
                raise PncpColetaError(
                    "pagina vazia antes do fim informado pelo PNCP",
                    endpoint=ENDPOINT_PROPOSTAS_ABERTAS,
                    page=pagina,
                )
            if pagina >= resultado.total_paginas:
                break
        elif len(resultado.itens) < tamanho_pagina:
            break
        pagina += 1

    logger.info(
        "Coleta PNCP de propostas abertas concluida",
        extra={
            "job_id": job.id,
            "flow": "propostas_abertas",
            "uf": uf,
            "modalidade": modalidade,
            "data_final": data_final.isoformat(),
            "paginas_lidas": paginas_lidas,
            "registros_recebidos": total_registros,
            "raw_criados": raw_criados,
            "raw_reutilizados": raw_reutilizados,
            "jobs_normalizacao_criados": normalizacoes_criadas,
            "jobs_normalizacao_reutilizados": normalizacoes_reutilizadas,
            "cursor_confirmado": False,
        },
    )
    return total_registros


def tamanho_pagina_do_job(job: JobReivindicado, padrao: int = 50) -> int:
    valor = job.parameters.get("tamanhoPagina", padrao)
    if isinstance(valor, bool) or not isinstance(valor, int) or not 1 <= valor <= 50:
        raise ValueError("tamanhoPagina do job deve ser um inteiro entre 1 e 50")
    return valor


def coletar_publicacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 50) -> int:
    """Persiste todas as paginas; qualquer erro aborta o job sem confirmar janela."""
    fluxo_valido = (
        (job.job_type == "incremental" and job.scope_key.startswith("publicacao:"))
        or (job.job_type == "backfill" and job.scope_key.startswith("backfill:"))
    )
    if (
        job.source != FONTE
        or job.entity_type != TIPO
        or not fluxo_valido
        or job.window_start is None
        or job.window_end is None
        or job.window_start.tzinfo is None
        or job.window_end.tzinfo is None
        or job.window_end < job.window_start
    ):
        raise ValueError("job nao representa uma janela de publicacao PNCP")
    tamanho_pagina = tamanho_pagina_do_job(job, tamanho_pagina)
    try:
        modalidade = int(job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0])
        uf = job.scope_key.split(":uf:", 1)[1].split(":", 1)[0] if ":uf:" in job.scope_key else None
    except (IndexError, ValueError) as exc:
        raise ValueError("scope_key de publicacao invalido") from exc
    pagina = job.page if job.page is not None else 1
    if isinstance(pagina, bool) or pagina != 1:
        raise ValueError("coleta por janela deve reiniciar na pagina 1")
    total_registros = 0
    paginas_lidas = 0
    raw_criados = 0
    raw_reutilizados = 0
    normalizacoes_agendadas = 0
    normalizacoes_reutilizadas = 0

    def confirmar_cursor() -> int:
        escopo_cursor = f"uf:{uf}:modalidade:{modalidade}" if uf else f"modalidade:{modalidade}"
        confirmar_janela_job(
            ChaveCursor(FONTE, TIPO, "publicacao", escopo_cursor),
            final=job.window_end,
            job_id=job.id,
            lease_token=job.lease_token,
        )
        logger.info(
            "Coleta PNCP de publicacoes concluida",
            extra={
                "job_id": job.id,
                "flow": "publicacao",
                "uf": uf,
                "modalidade": modalidade,
                "window_start": job.window_start,
                "window_end": job.window_end,
                "paginas_lidas": paginas_lidas,
                "registros_recebidos": total_registros,
                "raw_criados": raw_criados,
                "raw_reutilizados": raw_reutilizados,
                "jobs_normalizacao_criados": normalizacoes_agendadas,
                "jobs_normalizacao_reutilizados": normalizacoes_reutilizadas,
                "cursor_confirmado": True,
            },
        )
        return total_registros

    while True:
        inicio_pagina = time.monotonic()
        resultado = cliente.buscar_publicacoes(inicio=job.window_start.date(), fim=job.window_end.date(), modalidade=modalidade, uf=uf, pagina=pagina, tamanho_pagina=tamanho_pagina)
        registros_pagina = len(resultado.itens)
        raw_criados_pagina = 0
        raw_reutilizados_pagina = 0
        normalizacoes_agendadas_pagina = 0
        normalizacoes_reutilizadas_pagina = 0
        # Vazia e valida somente encerra quando e a primeira pagina ou apos pagina conhecida.
        for item in resultado.itens:
            external_id, _ = identificar_contratacao_raw(item)
            with orm.main_session() as session:
                persistencia = salvar_raw_e_agendar_normalizacao_session(
                    session,
                    RegistroRawPncp(
                        entity_type=TIPO,
                        external_id=external_id,
                        payload=item,
                        collected_at=datetime.now(timezone.utc),
                        source_updated_at=data_atualizacao_fonte(item.get("dataAtualizacao")),
                        run_id=f"job:{job.id}:pagina:{pagina}",
                    ),
                )
            total_registros += 1
            raw_criados_pagina += int(persistencia.raw_criado)
            raw_reutilizados_pagina += int(not persistencia.raw_criado)
            normalizacoes_agendadas_pagina += int(persistencia.job_criado)
            normalizacoes_reutilizadas_pagina += int(not persistencia.job_criado)

        paginas_lidas += 1
        raw_criados += raw_criados_pagina
        raw_reutilizados += raw_reutilizados_pagina
        normalizacoes_agendadas += normalizacoes_agendadas_pagina
        normalizacoes_reutilizadas += normalizacoes_reutilizadas_pagina
        logger.info(
            "Pagina PNCP de publicacoes processada",
            extra={
                "job_id": job.id,
                "flow": "publicacao",
                "uf": uf,
                "modalidade": modalidade,
                "window_start": job.window_start,
                "window_end": job.window_end,
                "pagina": pagina,
                "paginas_totais": resultado.total_paginas,
                "paginas_restantes": (
                    max(0, resultado.total_paginas - pagina)
                    if resultado.total_paginas is not None else None
                ),
                "registros_pagina": registros_pagina,
                "registros_acumulados": total_registros,
                "raw_criados_pagina": raw_criados_pagina,
                "raw_reutilizados_pagina": raw_reutilizados_pagina,
                "jobs_normalizacao_criados_pagina": normalizacoes_agendadas_pagina,
                "jobs_normalizacao_reutilizados_pagina": normalizacoes_reutilizadas_pagina,
                "duracao_pagina_segundos": round(time.monotonic() - inicio_pagina, 3),
            },
        )
        if resultado.total_paginas is not None:
            if not resultado.itens and pagina < resultado.total_paginas:
                raise PncpColetaError("pagina vazia antes do fim informado pelo PNCP", endpoint=ENDPOINT_PUBLICACAO, page=pagina)
            if pagina >= resultado.total_paginas:
                return confirmar_cursor()
        elif len(resultado.itens) < tamanho_pagina:
            return confirmar_cursor()
        elif not resultado.itens:
            return confirmar_cursor()
        pagina += 1

