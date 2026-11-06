"""Enriquecimento PNCP sob demanda, separado da descoberta e normalização."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import time
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import PncpEnriquecimento, PncpOportunidade, RawPncpRecord
from app.pncp.jobs import JobReivindicado, NovoJob, agendar_job_session
from app.pncp.retry import classificar_status_http, retry_after_para_timedelta
from app.pncp.protecao import PncpProtegidoError, ProtecaoPncp
from app.pncp.participation import salvar_documentos_session, salvar_itens_session, salvar_resultados_session


PRIORIDADE_ENRIQUECIMENTO = 40
RECURSOS = frozenset({"detail", "items", "documents"})


class ErroEnriquecimentoPncp(RuntimeError):
    def __init__(self, mensagem: str, *, retryable: bool = True, status_code: int | None = None, retry_after: timedelta | None = None) -> None:
        super().__init__(mensagem)
        self.retryable, self.status_code, self.retry_after = retryable, status_code, retry_after

    def contexto(self) -> dict[str, object]:
        return {"categoria": "enriquecimento", "status_code": self.status_code, "retryable": self.retryable,
            "retry_after_seconds": self.retry_after.total_seconds() if self.retry_after else None}


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
    cache_valido = bool(
        cache
        and cache.source_version_hash == oportunidade.payload_hash
        and cache.expires_at > agora
    )
    resultados = []
    for recurso in RECURSOS:
        if cache_valido and getattr(cache, f"{recurso}_hash", None) is not None:
            continue
        scope = f"enrich:{recurso}:{oportunidade.external_id}:{oportunidade.payload_hash}"
        resultados.append(agendar_job_session(session, NovoJob(
            job_type="enrich", entity_type="CONTRATACAO", source=oportunidade.source,
            scope_key=scope, dedupe_key=scope, priority=PRIORIDADE_ENRIQUECIMENTO,
            next_run_at=agora, raw_record_id=oportunidade.raw_record_id,
        )))
    return resultados


class ClienteEnriquecimentoPncp:
    """Cliente somente GET das rotas oficiais de contratação; não consulta PCA."""

    def __init__(self, base_url: str | None = None, *, session: requests.Session | None = None, protecao: ProtecaoPncp | None = None) -> None:
        self.base_url = (base_url or os.getenv("PNCP_ENRICHMENT_BASE_URL", "https://pncp.gov.br/api/pncp")).rstrip("/")
        self.timeout = (float(os.getenv("PNCP_CONNECT_TIMEOUT_SECONDS", "10")), float(os.getenv("PNCP_READ_TIMEOUT_SECONDS", "30")))
        self.session = session or requests.Session()
        self.protecao = protecao or ProtecaoPncp()

    def _buscar_json(self, caminho: str, recurso: str) -> Any:
        limite_espera = time.monotonic() + 30
        while True:
            permissao = self.protecao.adquirir()
            if permissao.permitida:
                break
            if (
                permissao.motivo not in {"rate limit global", "limite de concorrencia"}
                or time.monotonic() + permissao.aguardar.total_seconds() > limite_espera
            ):
                raise PncpProtegidoError(permissao)
            time.sleep(max(0.01, permissao.aguardar.total_seconds()))
        inicio = time.monotonic()
        try:
            resposta = self.session.get(f"{self.base_url}{caminho}", timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.RequestException as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, latency_seconds=time.monotonic() - inicio)
            raise ErroEnriquecimentoPncp(type(exc).__name__) from exc
        if resposta.status_code >= 400:
            retryable = classificar_status_http(resposta.status_code)
            self.protecao.registrar_resultado(sucesso=False, retryable=retryable, status_code=resposta.status_code, latency_seconds=time.monotonic() - inicio)
            raise ErroEnriquecimentoPncp(f"HTTP {resposta.status_code} em {recurso}", retryable=retryable, status_code=resposta.status_code,
                retry_after=retry_after_para_timedelta(resposta.headers.get("Retry-After")) if resposta.status_code == 429 else None)
        try:
            payload = resposta.json(parse_float=str) if recurso in {"items", "results"} else resposta.json()
        except ValueError as exc:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=resposta.status_code, latency_seconds=time.monotonic() - inicio)
            raise ErroEnriquecimentoPncp("JSON invalido em enriquecimento") from exc
        esperado = (payload if recurso == "detail" else
            payload if isinstance(payload, list) else
            payload.get({"items": "itens", "documents": "documentos", "results": "listaResultados"}[recurso]) if isinstance(payload, dict) else None)
        valido = isinstance(esperado, dict) if recurso == "detail" else isinstance(esperado, list) and all(isinstance(item, dict) for item in esperado)
        if not valido:
            self.protecao.registrar_resultado(sucesso=False, retryable=True, status_code=resposta.status_code, latency_seconds=time.monotonic() - inicio)
            raise ErroEnriquecimentoPncp("estrutura JSON invalida em enriquecimento")
        self.protecao.registrar_resultado(sucesso=True, status_code=resposta.status_code, latency_seconds=time.monotonic() - inicio)
        return payload

    def buscar(self, identidade: IdentidadeCompra, recurso: str) -> dict[str, Any] | list[dict[str, Any]]:
        if recurso not in RECURSOS:
            raise ValueError("recurso de enriquecimento invalido")
        raiz = f"/v1/orgaos/{identidade.cnpj}/compras/{identidade.ano}/{identidade.sequencial}"
        caminho = raiz if recurso == "detail" else f"{raiz}/{'itens' if recurso == 'items' else 'arquivos'}"
        if recurso == "items":
            tamanho = 50
            itens: list[dict[str, Any]] = []
            for pagina in range(1, int(os.getenv("PNCP_ENRICHMENT_MAX_ITEM_PAGES", "100")) + 1):
                payload = self._buscar_json(f"{caminho}?pagina={pagina}&tamanhoPagina={tamanho}", recurso)
                lote = payload if isinstance(payload, list) else payload.get("itens") if isinstance(payload, dict) else None
                if not isinstance(lote, list) or any(not isinstance(item, dict) for item in lote):
                    raise ErroEnriquecimentoPncp("pagina de itens PNCP invalida", retryable=False)
                itens.extend(lote)
                if len(lote) < tamanho:
                    return itens
            raise ErroEnriquecimentoPncp("limite de paginas de itens excedido", retryable=False)
        payload = self._buscar_json(caminho, recurso)
        if recurso == "detail" and not isinstance(payload, dict):
            raise ErroEnriquecimentoPncp("detalhe PNCP invalido")
        if recurso == "documents" and isinstance(payload, dict):
            payload = payload.get("documentos")
        if recurso != "detail" and not isinstance(payload, list):
            raise ErroEnriquecimentoPncp("lista PNCP invalida")
        return payload

    def buscar_resultados(self, identidade: IdentidadeCompra, numero_item: int) -> list[dict[str, Any]]:
        raiz = f"/v1/orgaos/{identidade.cnpj}/compras/{identidade.ano}/{identidade.sequencial}"
        payload = self._buscar_json(f"{raiz}/itens/{numero_item}/resultados", "results")
        resultados = payload if isinstance(payload, list) else payload.get("listaResultados") if isinstance(payload, dict) else None
        if not isinstance(resultados, list) or any(not isinstance(item, dict) for item in resultados):
            raise ErroEnriquecimentoPncp("resultados PNCP invalidos", retryable=False)
        return resultados


def salvar_recurso_session(
    session: Session, *, oportunidade: PncpOportunidade, identidade: IdentidadeCompra,
    recurso: str, payload: dict[str, Any] | list[dict[str, Any]], agora: datetime | None = None,
    observacao: str | None = None,
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
    if recurso == "items":
        salvar_itens_session(session, oportunidade, payload)
    elif recurso == "documents":
        salvar_documentos_session(session, oportunidade, payload, observacao=observacao)


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
            salvar_recurso_session(session, oportunidade=oportunidade, identidade=identidade, recurso=recurso, payload=payload, observacao=str(job.id))
            if recurso == "items":
                from app.nlp.corpus import sincronizar_corpus

                session.flush()
                sincronizar_corpus(session, opportunity_keys=[(job.source, external_id)])
    if recurso == "items":
        for item_payload in payload:
            numero_item = item_payload["numeroItem"]
            resultados = [] if item_payload.get("temResultado") is False else cliente.buscar_resultados(identidade, numero_item)
            with orm.main_session() as session:
                oportunidade = session.get(PncpOportunidade, {"source": job.source, "external_id": external_id})
                if oportunidade is not None and oportunidade.payload_hash == versao:
                    salvar_resultados_session(session, oportunidade, numero_item, resultados, tem_resultado=item_payload.get("temResultado"))
