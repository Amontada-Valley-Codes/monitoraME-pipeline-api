"""Fluxo independente de atualizacoes globais do PNCP."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core import orm
from app.core.models import PncpContratacaoAtual
from app.pncp.ingestion import ClientePncp, PncpColetaError, UFS_NORDESTE, tamanho_pagina_do_job
from app.pncp.jobs import JobReivindicado, NovoJob, PNCP_QUERY_CONFIG_VERSION
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session
from app.pncp.checkpoints import ChaveCursor, confirmar_janela

FLOW = "atualizacao"

def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str): return None
    try: return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError: return None

def agendar_atualizacao(*, inicio: datetime, fim: datetime, modalidades: list[int], ufs: tuple[str, ...] | list[str] | None = None, tamanho_pagina: int = 50, prioridade: int = 80) -> list[NovoJob]:
    if inicio.tzinfo is None or fim.tzinfo is None or fim < inicio:
        raise ValueError("janela com timezone e ordem valida e obrigatoria")
    if not 1 <= tamanho_pagina <= 50:
        raise ValueError("tamanho_pagina deve estar entre 1 e 50")
    if ufs is not None and (not ufs or any(uf not in UFS_NORDESTE for uf in ufs)):
        raise ValueError("ufs deve conter somente UFs do Nordeste")
    jobs: list[NovoJob] = []
    for uf in (ufs or (None,)):
        for modalidade in modalidades:
            uf_scope = f":uf:{uf}" if uf is not None else ""
            scope = f"atualizacao:{inicio.date().isoformat()}:{fim.date().isoformat()}{uf_scope}:modalidade:{modalidade}:tamanho:{tamanho_pagina}"
            dedupe_key = f"{scope}:config:{PNCP_QUERY_CONFIG_VERSION}"
            jobs.append(NovoJob("incremental", "CONTRATACAO", "PNCP", scope, dedupe_key, prioridade, datetime.now(timezone.utc), window_start=inicio, window_end=fim, page=1, parameters={"flow": FLOW, "codigoModalidadeContratacao": modalidade, "uf": uf, "tamanhoPagina": tamanho_pagina}, config_version=PNCP_QUERY_CONFIG_VERSION))
    return jobs

def salvar_versao_atual_session(session: Session, item: dict[str, Any], payload_hash: str, source_updated_at: datetime | None) -> str:
    external_id = str(item["numeroControlePNCP"])
    anterior = session.get(PncpContratacaoAtual, {"source": "PNCP", "external_id": external_id})
    if anterior and anterior.payload_hash == payload_hash: return "INALTERADO"
    if anterior and anterior.source_updated_at and (source_updated_at is None or source_updated_at < anterior.source_updated_at): return "ANTIGO"
    session.execute(insert(PncpContratacaoAtual).values(source="PNCP", external_id=external_id, payload_hash=payload_hash, source_updated_at=source_updated_at, payload=item).on_conflict_do_update(index_elements=["source", "external_id"], set_={"payload_hash": payload_hash, "source_updated_at": source_updated_at, "payload": item, "updated_at": text("now()")}))
    return "NOVO" if anterior is None else "ALTERADO"

def coletar_atualizacao(job: JobReivindicado, cliente: ClientePncp, *, tamanho_pagina: int = 50) -> dict[str, int]:
    if job.window_start is None or job.window_end is None: raise ValueError("janela obrigatoria")
    tamanho_pagina = tamanho_pagina_do_job(job, tamanho_pagina)
    modalidade = int(job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0]) if ":modalidade:" in job.scope_key else int(job.scope_key.rsplit(":", 1)[1]); uf = job.scope_key.split(":uf:", 1)[1].split(":", 1)[0] if ":uf:" in job.scope_key else None; pagina = job.page or 1; totais = {"NOVO": 0, "ALTERADO": 0, "INALTERADO": 0}
    while True:
        resposta = cliente.buscar_atualizacoes(inicio=job.window_start.date(), fim=job.window_end.date(), modalidade=modalidade, uf=uf, pagina=pagina, tamanho_pagina=tamanho_pagina)
        if not resposta.itens and resposta.total_paginas and pagina < resposta.total_paginas: raise PncpColetaError("pagina de atualizacao incompleta")
        for item in resposta.itens:
            if not isinstance(item.get("numeroControlePNCP"), str): raise PncpColetaError("atualizacao sem numeroControlePNCP")
            atualizado = _data(item.get("dataAtualizacao"))
            with orm.main_session() as s:
                raw = salvar_raw_e_agendar_normalizacao_session(s, RegistroRawPncp("CONTRATACAO", item["numeroControlePNCP"], item, datetime.now(timezone.utc), atualizado, f"job:{job.id}:pagina:{pagina}"))
                classe = salvar_versao_atual_session(s, item, raw.payload_hash, atualizado)
            if classe != "ANTIGO": totais[classe] += 1
        if resposta.total_paginas is not None and pagina >= resposta.total_paginas: break
        if resposta.total_paginas is None and len(resposta.itens) < tamanho_pagina: break
        pagina += 1
    escopo_cursor = f"uf:{uf}:modalidade:{modalidade}" if uf else f"modalidade:{modalidade}"
    confirmar_janela(ChaveCursor("PNCP", "CONTRATACAO", FLOW, escopo_cursor), final=job.window_end, run_id=str(job.id))
    return totais
