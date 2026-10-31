"""Consultas locais de oportunidades PNCP para consumidores da plataforma."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.core import orm
from app.core.models import IngestionJob, PncpDocumento, PncpEnriquecimento, PncpItem, PncpOportunidade, PncpResultadoItem, RawPncpRecord
from app.pncp.opportunities import contrato_consumo


def listar_editais(
    *,
    busca: str | None = None,
    pagina: int = 1,
    tamanho_pagina: int = 20,
    ano_publicacao: int | None = None,
    ano_compra: int | None = None,
) -> dict[str, Any]:
    """Lista dados normalizados locais; esta leitura nunca consulta o PNCP."""
    filtros = [PncpOportunidade.source == "PNCP"]
    termo = (busca or "").strip()
    if termo:
        padrao = f"%{termo}%"
        filtros.append(
            or_(
                PncpOportunidade.external_id.ilike(padrao),
                PncpOportunidade.objeto.ilike(padrao),
            )
        )
    if ano_publicacao is not None:
        filtros.extend((
            PncpOportunidade.data_publicacao >= date(ano_publicacao, 1, 1),
            PncpOportunidade.data_publicacao < date(ano_publicacao + 1, 1, 1),
        ))
    if ano_compra is not None:
        filtros.append(PncpOportunidade.ano_compra == ano_compra)

    with orm.main_session() as session:
        total = session.scalar(
            select(func.count())
            .select_from(PncpOportunidade)
            .where(*filtros)
        ) or 0
        consulta = (
            select(PncpOportunidade, PncpEnriquecimento)
            .outerjoin(
                PncpEnriquecimento,
                and_(
                    PncpEnriquecimento.source == PncpOportunidade.source,
                    PncpEnriquecimento.external_id == PncpOportunidade.external_id,
                ),
            )
            .where(*filtros)
            .order_by(
                PncpOportunidade.prazo_proposta_final.asc().nulls_last(),
                PncpOportunidade.normalized_at.desc(),
            )
            .offset((pagina - 1) * tamanho_pagina)
            .limit(tamanho_pagina)
        )
        registros = session.execute(consulta).all()

    return {
        "dados": [contrato_consumo(oportunidade, enriquecimento) for oportunidade, enriquecimento in registros],
        "paginacao": {
            "pagina": pagina,
            "tamanho_pagina": tamanho_pagina,
            "total": total,
        },
    }


def obter_edital(external_id: str) -> dict[str, Any]:
    """Retorna um edital e seus detalhes locais, se já enriquecidos."""
    with orm.main_session() as session:
        oportunidade = session.get(
            PncpOportunidade,
            {"source": "PNCP", "external_id": external_id},
        )
        if oportunidade is None:
            raise LookupError("Edital PNCP nao encontrado.")
        enriquecimento = session.get(
            PncpEnriquecimento,
            {"source": "PNCP", "external_id": external_id},
        )
        itens = session.scalars(select(PncpItem).where(PncpItem.source == "PNCP", PncpItem.external_id == external_id).order_by(PncpItem.numero_item)).all()
        resultados = session.scalars(select(PncpResultadoItem).where(PncpResultadoItem.source == "PNCP", PncpResultadoItem.external_id == external_id, PncpResultadoItem.disponibilidade == "ATUAL").order_by(PncpResultadoItem.numero_item, PncpResultadoItem.sequencial_resultado)).all()
        documentos = session.scalars(select(PncpDocumento).where(PncpDocumento.source == "PNCP", PncpDocumento.external_id == external_id).order_by(PncpDocumento.sequencial_documento)).all()

    resultado = contrato_consumo(oportunidade, enriquecimento)
    resultado["detalhe"] = enriquecimento.detail_payload if enriquecimento else None
    resultado["itens"] = enriquecimento.items_payload if enriquecimento else None
    resultado["anexos"] = enriquecimento.documents_payload if enriquecimento else None
    resultado["itens_estruturados"] = [{"numero_item": item.numero_item, "numero_lote": item.numero_lote,
        "descricao": item.descricao, "quantidade": item.quantidade, "unidade": item.unidade,
        "valor_unitario_estimado": item.valor_unitario_estimado, "valor_total_estimado": item.valor_total_estimado,
        "situacao": item.situacao, "criterio_julgamento": item.criterio_julgamento,
        "resultado_status": item.resultado_status, "revisao": item.revisao} for item in itens]
    resultado["resultados_por_item"] = [{"numero_item": r.numero_item, "sequencial_resultado": r.sequencial_resultado,
        "fornecedor": r.fornecedor, "cnpj_fornecedor": r.cnpj_fornecedor,
        "valor_unitario_homologado": r.valor_unitario_homologado, "quantidade_homologada": r.quantidade_homologada,
        "situacao": r.situacao, "data_resultado": r.data_resultado, "revisao": r.revisao} for r in resultados]
    resultado["documentos_manifesto"] = [{"sequencial_documento": d.sequencial_documento, "titulo": d.titulo,
        "tipo": d.tipo, "url_oficial": d.url_oficial, "publicado_em": d.publicado_em,
        "hash_metadados": d.payload_hash, "disponibilidade": d.disponibilidade, "revisao": d.revisao} for d in documentos]
    lotes: dict[str, list[int]] = {}
    for item in itens:
        if item.numero_lote is not None:
            lotes.setdefault(item.numero_lote, []).append(item.numero_item)
    resultado["lotes_identificados"] = [{"numero_lote": numero, "numeros_itens": numeros}
        for numero, numeros in sorted(lotes.items())]
    resultado["cobertura_participacao"] = {"itens": len(itens), "resultados": len(resultados),
        "lotes_identificados": len(lotes),
        "itens_sem_resultado": sum(item.resultado_status == "AUSENTE" for item in itens),
        "itens_resultado_parcial": sum(item.resultado_status == "PARCIAL" for item in itens),
        "documentos": len(documentos)}
    return resultado


def obter_cobertura_anual(ano: int) -> dict[str, Any]:
    """Resume o estado persistido dos jobs de backfill de publicacao do ano."""
    with orm.main_session() as session:
        jobs = session.scalars(
            select(IngestionJob)
            .where(IngestionJob.job_type == "backfill", IngestionJob.scope_key.like(f"backfill:{ano}:publicacao:%"))
            .order_by(IngestionJob.window_start, IngestionJob.scope_key)
        ).all()
        data_publicacao = RawPncpRecord.payload.op("->>")("dataPublicacaoPncp")
        normalizacao_rows = session.execute(
            select(IngestionJob.status, func.count())
            .join(RawPncpRecord, RawPncpRecord.id == IngestionJob.raw_record_id)
            .where(
                IngestionJob.job_type == "normalize",
                data_publicacao.like(f"{ano}-%"),
            )
            .group_by(IngestionJob.status)
        ).all()
    contagens = {status: 0 for status in ("PENDING", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "QUARANTINED")}
    janelas = []
    for job in jobs:
        contagens[job.status] = contagens.get(job.status, 0) + 1
        modalidade = job.scope_key.split(":modalidade:", 1)[1].split(":", 1)[0]
        janelas.append({
            "inicio": job.window_start,
            "fim": job.window_end,
            "modalidade": modalidade,
            "status": job.status,
            "tentativas": job.attempts,
            "erro": job.last_error,
        })
    completos = contagens["SUCCEEDED"]
    normalizacao = {status: 0 for status in ("PENDING", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "QUARANTINED")}
    for status, quantidade in normalizacao_rows:
        normalizacao[status] = int(quantidade)
    coleta_completa = bool(jobs) and completos == len(jobs)
    normalizacao_completa = (
        sum(normalizacao.values()) > 0
        and normalizacao["SUCCEEDED"] == sum(normalizacao.values())
    )
    return {
        "ano": ano,
        "cobertura_completa": coleta_completa and normalizacao_completa,
        "coleta_completa": coleta_completa,
        "normalizacao_completa": normalizacao_completa,
        "janelas_total": len(jobs),
        "janelas_concluidas": completos,
        "status": contagens,
        "normalizacao": normalizacao,
        "janelas": janelas,
    }
