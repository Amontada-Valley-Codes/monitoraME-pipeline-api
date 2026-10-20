"""Coleta incremental da listagem publica de contratacoes do PNCP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from typing import Any

import requests

from app.core import orm
from app.pncp.jobs import JobReivindicado, NovoJob
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session


ENDPOINT_PUBLICACAO = "/v1/contratacoes/publicacao"
FONTE = "PNCP"
TIPO = "CONTRATACAO"


class PncpColetaError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaginaContratacoes:
    itens: list[dict[str, Any]]
    total_paginas: int | None


class ClientePncp:
    def __init__(self, base_url: str | None = None, *, timeout: tuple[float, float] | None = None, session: requests.Session | None = None) -> None:
        self.base_url = (base_url or os.getenv("PNCP_BASE_URL", "https://pncp.gov.br/api/consulta")).rstrip("/")
        self.timeout = timeout or (float(os.getenv("PNCP_CONNECT_TIMEOUT_SECONDS", "10")), float(os.getenv("PNCP_READ_TIMEOUT_SECONDS", "30")))
        self.session = session or requests.Session()

    def buscar_publicacoes(self, *, inicio: date, fim: date, modalidade: int, pagina: int, tamanho_pagina: int) -> PaginaContratacoes:
        if pagina < 1 or tamanho_pagina < 1 or tamanho_pagina > 500:
            raise ValueError("pagina deve ser positiva e tamanho_pagina deve estar entre 1 e 500")
        params = {"dataInicial": inicio.strftime("%Y%m%d"), "dataFinal": fim.strftime("%Y%m%d"), "codigoModalidadeContratacao": modalidade, "pagina": pagina, "tamanhoPagina": tamanho_pagina}
        try:
            resposta = self.session.get(f"{self.base_url}{ENDPOINT_PUBLICACAO}", params=params, timeout=self.timeout, headers={"Accept": "application/json"})
            resposta.raise_for_status()
            corpo = resposta.json()
        except (requests.RequestException, ValueError) as exc:
            raise PncpColetaError(f"falha na pagina {pagina}: {type(exc).__name__}") from exc
        if not isinstance(corpo, dict):
            raise PncpColetaError("resposta PNCP nao e objeto JSON")
        itens = corpo.get("data", corpo.get("items"))
        if not isinstance(itens, list) or any(not isinstance(item, dict) for item in itens):
            raise PncpColetaError("resposta PNCP sem lista valida de contratacoes")
        total = corpo.get("totalPaginas", corpo.get("totalPages"))
        if total is not None and (not isinstance(total, int) or total < pagina):
            raise PncpColetaError("metadado de paginacao PNCP invalido")
        return PaginaContratacoes(itens, total)


def agendar_publicacao(*, inicio: datetime, fim: datetime, modalidades: list[int], tamanho_pagina: int = 100, prioridade: int = 70) -> list[NovoJob]:
    if inicio.tzinfo is None or fim.tzinfo is None or fim < inicio:
        raise ValueError("janela com timezone e ordem valida e obrigatoria")
    jobs: list[NovoJob] = []
    for modalidade in modalidades:
        scope = f"publicacao:{inicio.date().isoformat()}:{fim.date().isoformat()}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
        jobs.append(NovoJob("incremental", TIPO, FONTE, scope, scope, prioridade, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1))
    return jobs


def coletar_publicacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 100) -> int:
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
                return total_registros
        elif len(resultado.itens) < tamanho_pagina:
            return total_registros
        elif not resultado.itens:
            return total_registros
        pagina += 1

