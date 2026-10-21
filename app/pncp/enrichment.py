"""Enriquecimento PNCP sob demanda, separado da descoberta e normalização."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import PncpEnriquecimento, PncpOportunidade, RawPncpRecord
from app.pncp.jobs import JobReivindicado, NovoJob, agendar_job_session
from app.pncp.retry import classificar_status_http, retry_after_para_timedelta


PRIORIDADE_ENRIQUECIMENTO = 40
RECURSOS = frozenset({"detail", "items", "documents"})


class ErroEnriquecimentoPncp(RuntimeError):
    def __init__(self, mensagem: str, *, retryable: bool = True, status_code: int | None = None) -> None:
        super().__init__(mensagem)
        self.retryable, self.status_code = retryable, status_code

    def contexto(self) -> dict[str, object]:
        return {"categoria": "enriquecimento", "status_code": self.status_code, "retryable": self.retryable}


@dataclass(frozen=True)
class IdentidadeCompra:
    cnpj: str
    ano: int
    sequencial: int


def identidade_do_payload(payload: dict[str, Any]) -> IdentidadeCompra:
    orgao = payload.get("orgaoEntidade")
    cnpj = orgao.get("cnpj") if isinstance(orgao, dict) else payload.get("cnpjOrgao")
    cnpj = re.sub(r"\D", "", str(cnpj or ""))
    try:
        ano, sequencial = int(payload["anoCompra"]), int(payload["sequencialCompra"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ErroEnriquecimentoPncp("identidade PNCP incompleta", retryable=False) from exc
    if len(cnpj) != 14 or ano < 1900 or sequencial < 1:
        raise ErroEnriquecimentoPncp("identidade PNCP invalida", retryable=False)
    return IdentidadeCompra(cnpj, ano, sequencial)


def oportunidade_relevante(
    oportunidade: PncpOportunidade, *, solicitada: bool = False, acompanhada: bool = False,
    compativel: bool = False, recem_atualizada: bool = False,
) -> bool:
    situacao = oportunidade.situacao
    texto = json.dumps(situacao, ensure_ascii=False).upper() if situacao is not None else ""
    aberta = any(valor in texto for valor in ("ABERTA", "PUBLICADA", "PROPOSTA"))
    return solicitada or acompanhada or compativel or recem_atualizada or aberta


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def agendar_enriquecimento_session(
    session: Session, *, oportunidade: PncpOportunidade, motivo: str, agora: datetime | None = None
) -> list[tuple[int, bool]]:
    """Agenda apenas recursos de uma oportunidade já considerada relevante."""
    agora = agora or datetime.now(timezone.utc)
    raw = session.get(RawPncpRecord, oportunidade.raw_record_id)
    if raw is None:
        raise ValueError("RAW da oportunidade inexistente")
    identidade_do_payload(raw.payload)  # valida antes de criar qualquer job.
    cache = session.get(PncpEnriquecimento, {"source": oportunidade.source, "external_id": oportunidade.external_id})
    if cache and cache.source_version_hash == oportunidade.payload_hash and cache.expires_at > agora:
        return []
    resultados = []
    for recurso in RECURSOS:
        scope = f"enrich:{recurso}:{oportunidade.external_id}:{oportunidade.payload_hash}"
        resultados.append(agendar_job_session(session, NovoJob(
            job_type="enrich", entity_type="CONTRATACAO", source=oportunidade.source,
            scope_key=scope, dedupe_key=scope, priority=PRIORIDADE_ENRIQUECIMENTO,
            next_run_at=agora, raw_record_id=oportunidade.raw_record_id,
        )))
    return resultados


class ClienteEnriquecimentoPncp:
    """Cliente somente GET das rotas oficiais de contratação; não consulta PCA."""

    def __init__(self, base_url: str | None = None, *, session: requests.Session | None = None) -> None:
        self.base_url = (base_url or os.getenv("PNCP_ENRICHMENT_BASE_URL", "https://pncp.gov.br/api/pncp")).rstrip("/")
        self.timeout = (float(os.getenv("PNCP_CONNECT_TIMEOUT_SECONDS", "10")), float(os.getenv("PNCP_READ_TIMEOUT_SECONDS", "30")))
        self.session = session or requests.Session()

    def buscar(self, identidade: IdentidadeCompra, recurso: str) -> dict[str, Any] | list[dict[str, Any]]:
        if recurso not in RECURSOS:
            raise ValueError("recurso de enriquecimento invalido")
        raiz = f"/v1/orgaos/{identidade.cnpj}/compras/{identidade.ano}/{identidade.sequencial}"
        caminho = raiz if recurso == "detail" else f"{raiz}/{'itens' if recurso == 'items' else 'arquivos'}"
        try:
            resposta = self.session.get(f"{self.base_url}{caminho}", timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.RequestException as exc:
            raise ErroEnriquecimentoPncp(type(exc).__name__) from exc
        if resposta.status_code >= 400:
            raise ErroEnriquecimentoPncp(f"HTTP {resposta.status_code} em {recurso}", retryable=classificar_status_http(resposta.status_code), status_code=resposta.status_code)
        try:
            payload = resposta.json()
        except ValueError as exc:
            raise ErroEnriquecimentoPncp("JSON invalido em enriquecimento") from exc
        if recurso == "detail" and not isinstance(payload, dict):
            raise ErroEnriquecimentoPncp("detalhe PNCP invalido")
        if recurso != "detail" and not isinstance(payload, list):
            raise ErroEnriquecimentoPncp("lista PNCP invalida")
        return payload


def salvar_recurso_session(
    session: Session, *, oportunidade: PncpOportunidade, identidade: IdentidadeCompra,
    recurso: str, payload: dict[str, Any] | list[dict[str, Any]], agora: datetime | None = None,
) -> None:
    agora = agora or datetime.now(timezone.utc)
    enriquecimento = session.get(PncpEnriquecimento, {"source": oportunidade.source, "external_id": oportunidade.external_id})
    if enriquecimento is None:
        enriquecimento = PncpEnriquecimento(source=oportunidade.source, external_id=oportunidade.external_id,
            cnpj_orgao=identidade.cnpj, ano_compra=identidade.ano, sequencial_compra=identidade.sequencial,
            source_version_hash=oportunidade.payload_hash, expires_at=agora + timedelta(hours=int(os.getenv("PNCP_ENRICHMENT_TTL_HOURS", "24"))))
        session.add(enriquecimento)
    enriquecimento.source_version_hash, enriquecimento.expires_at, enriquecimento.last_error = oportunidade.payload_hash, agora + timedelta(hours=int(os.getenv("PNCP_ENRICHMENT_TTL_HOURS", "24"))), None
    setattr(enriquecimento, f"{recurso}_payload", payload)
    setattr(enriquecimento, f"{recurso}_hash", _hash(payload))


def enriquecer_job(job: JobReivindicado, cliente: ClienteEnriquecimentoPncp) -> None:
    if job.job_type != "enrich" or job.raw_record_id is None:
        raise ValueError("job de enriquecimento invalido")
    partes = job.scope_key.split(":", 3)
    if len(partes) != 4 or partes[0] != "enrich" or partes[1] not in RECURSOS:
        raise ValueError("scope_key de enriquecimento invalido")
    recurso, external_id, versao = partes[1], partes[2], partes[3]
    with orm.main_session() as session:
        oportunidade = session.get(PncpOportunidade, {"source": job.source, "external_id": external_id})
        raw = session.get(RawPncpRecord, job.raw_record_id)
        if oportunidade is None or raw is None or oportunidade.payload_hash != versao:
            return  # versão tornou-se obsoleta; não substitui enriquecimento atual.
        identidade = identidade_do_payload(raw.payload)
    payload = cliente.buscar(identidade, recurso)
    with orm.main_session() as session:
        oportunidade = session.get(PncpOportunidade, {"source": job.source, "external_id": external_id})
        if oportunidade is not None and oportunidade.payload_hash == versao:
            salvar_recurso_session(session, oportunidade=oportunidade, identidade=identidade, recurso=recurso, payload=payload)
