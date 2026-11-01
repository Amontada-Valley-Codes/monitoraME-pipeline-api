"""Recuperação Top-K por CNAE usando pgvector no PostgreSQL Analytics."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from typing import Any, Sequence

from sqlalchemy import literal_column, select, text
from sqlalchemy.orm import Session

from app.core.models import NlpProcurementEmbedding, NlpProcurementSegment, PncpOportunidade
from app.nlp.embeddings.model import DIMENSAO_EMBEDDING


@dataclass(frozen=True)
class ConsultaCnae:
    """Embedding query de um CNAE do perfil, sem concatenação entre atividades."""

    codigo: str
    vector: list[float]


@dataclass
class CandidatoContratacao:
    source: str
    procurement_id: str
    score: float
    cnaes: set[str] = field(default_factory=set)
    segments: list[dict[str, Any]] = field(default_factory=list)


def _validar_vector(vector: Sequence[float]) -> None:
    if len(vector) != DIMENSAO_EMBEDDING:
        raise ValueError(f"embedding deve ter {DIMENSAO_EMBEDDING} dimensões")
    if not all(math.isfinite(float(value)) for value in vector):
        raise ValueError("embedding deve conter valores finitos")


def _buscar_por_cnae(
    session: Session,
    consulta: ConsultaCnae,
    *,
    model_version: str,
    fetch_k: int,
    segment_types: Sequence[str],
    modality_codes: Sequence[str] | None,
    ibge_prefixes: Sequence[str] | None,
    derived_statuses: Sequence[str] | None,
    procurement_ids: Sequence[str] | None,
    exact: bool,
) -> list[dict[str, Any]]:
    distancia = NlpProcurementEmbedding.vector.cosine_distance(consulta.vector)
    query = (
        select(
            NlpProcurementEmbedding.source,
            NlpProcurementEmbedding.external_id,
            NlpProcurementEmbedding.segment_type,
            NlpProcurementEmbedding.segment_key,
            (1.0 - distancia).label("score"),
        )
        .join(
            NlpProcurementSegment,
            (NlpProcurementSegment.source == NlpProcurementEmbedding.source)
            & (NlpProcurementSegment.external_id == NlpProcurementEmbedding.external_id)
            & (NlpProcurementSegment.segment_type == NlpProcurementEmbedding.segment_type)
            & (NlpProcurementSegment.segment_key == NlpProcurementEmbedding.segment_key)
            & (NlpProcurementSegment.text_hash == NlpProcurementEmbedding.text_hash),
        )
        .join(
            PncpOportunidade,
            (PncpOportunidade.source == NlpProcurementEmbedding.source)
            & (PncpOportunidade.external_id == NlpProcurementEmbedding.external_id),
        )
        .where(
            NlpProcurementEmbedding.model_version == model_version,
            NlpProcurementSegment.status == "READY",
            NlpProcurementEmbedding.segment_type.in_(segment_types),
        )
        .order_by(distancia)
        .limit(fetch_k)
    )
    if modality_codes:
        codigo_modalidade = PncpOportunidade.dados_semanticos.op("#>>")(
            literal_column("'{modalidade_codigo,valor}'")
        )
        query = query.where(codigo_modalidade.in_(list(modality_codes)))
    if ibge_prefixes:
        codigo_ibge = PncpOportunidade.unidade["codigoIbge"].as_string()
        query = query.where(
            _or_prefixes(codigo_ibge, ibge_prefixes)
        )
    if derived_statuses:
        query = query.where(PncpOportunidade.derived_status.in_(list(derived_statuses)))
    if procurement_ids is not None:
        if not procurement_ids:
            return []
        query = query.where(PncpOportunidade.external_id.in_(list(procurement_ids)))

    if not exact:
        # Filtros estruturados são opcionais. Iterative scan compensa o pós-filtro
        # do HNSW para modality/status/região sem reduzir silenciosamente o K.
        session.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"))
        session.execute(text("SELECT set_config('hnsw.ef_search', :value, true)"), {
            "value": str(max(40, min(fetch_k * 2, 1000)))
        })
        session.execute(text("SELECT set_config('hnsw.max_scan_tuples', '50000', true)"))
        session.execute(text("SELECT set_config('hnsw.scan_mem_multiplier', '2', true)"))
    else:
        # Benchmark de referência exata, sem usar o índice ANN.
        session.execute(text("SELECT set_config('enable_indexscan', 'off', true)"))
        session.execute(text("SELECT set_config('enable_bitmapscan', 'off', true)"))
    return [dict(row) for row in session.execute(query).mappings()]


def _or_prefixes(coluna: Any, prefixes: Sequence[str]) -> Any:
    from sqlalchemy import or_

    return or_(*(coluna.like(f"{prefix}%") for prefix in prefixes))


def buscar_contratacoes_top_k(
    session: Session,
    consultas: Sequence[ConsultaCnae],
    *,
    model_version: str,
    k: int | None = None,
    segment_types: Sequence[str] = ("OBJECT", "COMPLEMENTARY", "ITEM", "LOT"),
    modality_codes: Sequence[str] | None = None,
    ibge_prefixes: Sequence[str] | None = None,
    derived_statuses: Sequence[str] | None = None,
    procurement_ids: Sequence[str] | None = None,
    overfetch: int = 5,
    exact: bool = False,
) -> list[CandidatoContratacao]:
    """Recupera candidatos para vários CNAEs e consolida por contratação.

    K é por CNAE no banco; o resultado final é deduplicado e limitado a K
    contratações únicas, usando a maior similaridade entre CNAEs/segmentos.
    Filtros opcionais só restringem quando explicitamente fornecidos.
    """
    top_k = k if k is not None else int(os.getenv("NLP_RETRIEVAL_TOP_K", "20"))
    if top_k < 1 or overfetch < 1:
        raise ValueError("k e overfetch devem ser maiores que zero")
    if not consultas:
        return []
    if not segment_types:
        raise ValueError("segment_types deve conter ao menos um tipo")
    for consulta in consultas:
        _validar_vector(consulta.vector)

    consolidados: dict[tuple[str, str], CandidatoContratacao] = {}
    fetch_k = max(top_k, top_k * overfetch)
    for consulta in consultas:
        rows = _buscar_por_cnae(
            session,
            consulta,
            model_version=model_version,
            fetch_k=fetch_k,
            segment_types=segment_types,
            modality_codes=modality_codes,
            ibge_prefixes=ibge_prefixes,
            derived_statuses=derived_statuses,
            procurement_ids=procurement_ids,
            exact=exact,
        )
        for row in rows:
            key = (row["source"], row["external_id"])
            candidate = consolidados.get(key)
            if candidate is None:
                candidate = CandidatoContratacao(*key, float(row["score"]))
                consolidados[key] = candidate
            else:
                candidate.score = max(candidate.score, float(row["score"]))
            candidate.cnaes.add(consulta.codigo)
            candidate.segments.append({
                "segment_type": row["segment_type"],
                "segment_key": row["segment_key"],
                "score": float(row["score"]),
                "cnae": consulta.codigo,
            })
    ordenados = sorted(
        consolidados.values(),
        key=lambda item: (-item.score, item.source, item.procurement_id),
    )
    return ordenados[:top_k]
