"""Coleta incremental da listagem publica de contratacoes do PNCP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os
import time
from typing import Any

import requests

from app.core import orm
from app.pncp.jobs import JobReivindicado, NovoJob
from app.pncp.protecao import PncpProtegidoError, ProtecaoPncp
from app.pncp.retry import classificar_status_http, retry_after_para_timedelta
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session
from app.pncp.checkpoints import ChaveCursor, confirmar_janela


ENDPOINT_PUBLICACAO = "/v1/contratacoes/publicacao"
ENDPOINT_ATUALIZACAO = "/v1/contratacoes/atualizacao"
ENDPOINT_PROPOSTAS_ABERTAS = "/v1/contratacoes/proposta"
FONTE = "PNCP"
TIPO = "CONTRATACAO"


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
    ) -> None:
        super().__init__(mensagem)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.endpoint = endpoint

    def contexto(self) -> dict[str, object]:
        return {
            "categoria": "pncp_coleta",
            "endpoint": self.endpoint,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after.total_seconds() if self.retry_after else None,
        }


@dataclass(frozen=True)
class PaginaContratacoes:
    itens: list[dict[str, Any]]
    total_paginas: int | None


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

    def buscar_publicacoes(self, *, inicio: date, fim: date, modalidade: int, pagina: int, tamanho_pagina: int) -> PaginaContratacoes:
        parametros = {"dataInicial": inicio.strftime("%Y%m%d"), "dataFinal": fim.strftime("%Y%m%d"), "codigoModalidadeContratacao": modalidade}
        return self._buscar(ENDPOINT_PUBLICACAO, parametros=parametros, pagina=pagina, tamanho_pagina=tamanho_pagina)

    def buscar_atualizacoes(self, *, inicio: date, fim: date, modalidade: int, pagina: int, tamanho_pagina: int) -> PaginaContratacoes:
        parametros = {"dataInicial": inicio.strftime("%Y%m%d"), "dataFinal": fim.strftime("%Y%m%d"), "codigoModalidadeContratacao": modalidade}
        return self._buscar(ENDPOINT_ATUALIZACAO, parametros=parametros, pagina=pagina, tamanho_pagina=tamanho_pagina)

    def buscar_propostas_abertas(self, *, data_final: date, pagina: int, tamanho_pagina: int, modalidade: int | None = None) -> PaginaContratacoes:
        parametros: dict[str, Any] = {"dataFinal": data_final.strftime("%Y%m%d")}
        if modalidade is not None:
            parametros["codigoModalidadeContratacao"] = modalidade
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
        try:
            resposta = self.session.get(f"{self.base_url}{endpoint}", params=params, timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.Timeout as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError(f"falha na pagina {pagina}: timeout", endpoint=endpoint) from exc
        except requests.RequestException as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError(f"falha na pagina {pagina}: {type(exc).__name__}", endpoint=endpoint) from exc
        status = resposta.status_code
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
            )
        try:
            corpo = resposta.json()
        except ValueError as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError(f"falha na pagina {pagina}: JSON invalido", endpoint=endpoint) from exc
        if not isinstance(corpo, dict):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("resposta PNCP nao e objeto JSON")
        itens = corpo.get("data", corpo.get("items"))
        if not isinstance(itens, list) or any(not isinstance(item, dict) for item in itens):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("resposta PNCP sem lista valida de contratacoes")
        total = corpo.get("totalPaginas", corpo.get("totalPages"))
        if total is not None and (not isinstance(total, int) or total < pagina):
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
            raise PncpColetaError("metadado de paginacao PNCP invalido")
        self.protecao.registrar_resultado(sucesso=True, status_code=status, latency_seconds=time.monotonic() - inicio_chamada)
        return PaginaContratacoes(itens, total)


def agendar_publicacao(*, inicio: datetime, fim: datetime, modalidades: list[int], tamanho_pagina: int = 50, prioridade: int = 70) -> list[NovoJob]:
    if inicio.tzinfo is None or fim.tzinfo is None or fim < inicio:
        raise ValueError("janela com timezone e ordem valida e obrigatoria")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    jobs: list[NovoJob] = []
    for modalidade in modalidades:
        scope = f"publicacao:{inicio.date().isoformat()}:{fim.date().isoformat()}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
        jobs.append(NovoJob("incremental", TIPO, FONTE, scope, scope, prioridade, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1))
    return jobs


def agendar_backfill_anual(*, ano: int, modalidades: list[int], agora: datetime | None = None, tamanho_janela_dias: int = 7, tamanho_pagina: int = 50) -> list[NovoJob]:
    """Agenda o ano por janelas curtas para limitar páginas por execução."""
    if ano < 2000 or tamanho_janela_dias < 1 or not modalidades or tamanho_pagina < 1 or tamanho_pagina > 50:
        raise ValueError("ano, janela e tamanho de página inválidos")
    atual = agora or datetime.now(timezone.utc)
    if atual.tzinfo is None:
        raise ValueError("agora deve incluir timezone")
    inicio_ano = datetime(ano, 1, 1, tzinfo=atual.tzinfo)
    fim_ano = min(datetime(ano + 1, 1, 1, tzinfo=atual.tzinfo), atual)
    if fim_ano <= inicio_ano:
        return []
    jobs: list[NovoJob] = []
    inicio = inicio_ano
    while inicio < fim_ano:
        fim = min(inicio + timedelta(days=tamanho_janela_dias) - timedelta(seconds=1), fim_ano)
        for modalidade in modalidades:
            scope = f"backfill:{ano}:publicacao:{inicio.date().isoformat()}:{fim.date().isoformat()}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
            jobs.append(NovoJob("backfill", TIPO, FONTE, scope, scope, 20, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1))
        inicio = fim + timedelta(seconds=1)
    return jobs


def agendar_propostas_abertas(*, data_final: datetime, modalidades: list[int] | None = None, tamanho_pagina: int = 50, prioridade: int = 100) -> list[NovoJob]:
    if data_final.tzinfo is None:
        raise ValueError("data_final deve incluir timezone")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    codigos = modalidades or [None]
    jobs: list[NovoJob] = []
    for modalidade in codigos:
        codigo = str(modalidade) if modalidade is not None else "todas"
        scope = f"propostas-abertas:{data_final.date().isoformat()}:modalidade:{codigo}:tamanho:{tamanho_pagina}"
        jobs.append(NovoJob("incremental", TIPO, FONTE, scope, scope, prioridade, datetime.now(timezone.utc), window_end=data_final, page=1))
    return jobs


def coletar_propostas_abertas(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 50) -> int:
    """Persiste todas as páginas de propostas abertas como RAW e agenda normalização."""
    if job.source != FONTE or job.entity_type != TIPO or job.window_end is None:
        raise ValueError("job nao representa uma consulta de propostas abertas PNCP")
    try:
        modalidade_texto = job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0]
        modalidade = None if modalidade_texto == "todas" else int(modalidade_texto)
    except (IndexError, ValueError) as exc:
        raise ValueError("scope_key de propostas abertas invalido") from exc

    data_final = job.window_end.date()
    pagina = job.page or 1
    total_registros = 0
    while True:
        resultado = cliente.buscar_propostas_abertas(data_final=data_final, modalidade=modalidade, pagina=pagina, tamanho_pagina=tamanho_pagina)
        for item in resultado.itens:
            external_id = item.get("numeroControlePNCP")
            if not isinstance(external_id, str) or not external_id.strip():
                raise PncpColetaError("contratacao sem numeroControlePNCP valido", endpoint=ENDPOINT_PROPOSTAS_ABERTAS)
            with orm.main_session() as session:
                salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(entity_type=TIPO, external_id=external_id, payload=item, collected_at=datetime.now(timezone.utc), run_id=f"job:{job.id}:pagina:{pagina}"))
            total_registros += 1

        if resultado.total_paginas is not None:
            if not resultado.itens and pagina < resultado.total_paginas:
                raise PncpColetaError("pagina vazia antes do fim informado pelo PNCP", endpoint=ENDPOINT_PROPOSTAS_ABERTAS)
            if pagina >= resultado.total_paginas:
                return total_registros
        elif len(resultado.itens) < tamanho_pagina:
            return total_registros
        pagina += 1


def coletar_publicacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 50) -> int:
    """Persiste todas as paginas; qualquer erro aborta o job sem confirmar janela."""
    if job.source != FONTE or job.entity_type != TIPO or job.window_start is None or job.window_end is None:
        raise ValueError("job nao representa uma janela de publicacao PNCP")
    try:
        modalidade = int(job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("scope_key de publicacao invalido") from exc
    pagina = job.page or 1
    total_registros = 0
    while True:
        resultado = cliente.buscar_publicacoes(inicio=job.window_start.date(), fim=job.window_end.date(), modalidade=modalidade, pagina=pagina, tamanho_pagina=tamanho_pagina)
        # Vazia e valida somente encerra quando e a primeira pagina ou apos pagina conhecida.
        for item in resultado.itens:
            external_id = item.get("numeroControlePNCP")
            if not isinstance(external_id, str) or not external_id.strip():
                raise PncpColetaError("contratacao sem numeroControlePNCP valido")
            with orm.main_session() as session:
                salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(entity_type=TIPO, external_id=external_id, payload=item, collected_at=datetime.now(timezone.utc), run_id=f"job:{job.id}:pagina:{pagina}"))
            total_registros += 1
        if resultado.total_paginas is not None:
            if not resultado.itens and pagina < resultado.total_paginas:
                raise PncpColetaError("pagina vazia antes do fim informado pelo PNCP")
            if pagina >= resultado.total_paginas:
                confirmar_janela(ChaveCursor(FONTE, TIPO, "publicacao", f"modalidade:{modalidade}"), final=job.window_end, run_id=str(job.id))
                return total_registros
        elif len(resultado.itens) < tamanho_pagina:
            confirmar_janela(ChaveCursor(FONTE, TIPO, "publicacao", f"modalidade:{modalidade}"), final=job.window_end, run_id=str(job.id))
            return total_registros
        elif not resultado.itens:
            confirmar_janela(ChaveCursor(FONTE, TIPO, "publicacao", f"modalidade:{modalidade}"), final=job.window_end, run_id=str(job.id))
            return total_registros
        pagina += 1

