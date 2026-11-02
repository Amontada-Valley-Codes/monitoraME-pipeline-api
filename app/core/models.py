from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import VECTOR

from app.core.orm import MainBase


class IbgeMunicipio(MainBase):
    __tablename__ = "ibge_municipios"

    codigo_municipio: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    uf: Mapped[str] = mapped_column(Text)


class TceMunicipio(MainBase):
    __tablename__ = "tce_municipios"

    codigo_municipio_tce: Mapped[str] = mapped_column(Text, primary_key=True)
    codigo_municipio_ibge: Mapped[str] = mapped_column(Text, unique=True)


class FornecedorMe(MainBase):
    __tablename__ = "fornecedores_me"
    __table_args__ = (
        CheckConstraint("porte = 'ME'", name="ck_fornecedores_me_porte_me"),
    )

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    razao_social: Mapped[str | None] = mapped_column(Text)
    porte: Mapped[str] = mapped_column(Text)


class FornecedorCache(MainBase):
    __tablename__ = "fornecedores_cache"
    __table_args__ = (
        CheckConstraint(
            "opencnpj_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_status",
        ),
        CheckConstraint(
            "ultima_tentativa_status IN ('ok', 'nao_encontrado', 'indisponivel')",
            name="ck_fornecedores_cache_ultima_tentativa_status",
        ),
    )

    cnpj: Mapped[str] = mapped_column(Text, primary_key=True)
    opencnpj_status: Mapped[str] = mapped_column(Text)
    dados_normalizados: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    observado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultima_tentativa_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ultima_tentativa_status: Mapped[str] = mapped_column(Text)
    cache_desatualizado: Mapped[bool] = mapped_column(Boolean, default=False)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class ApiResponseCache(MainBase):
    """Resposta JSON calculada para uma combinação de método, rota e filtros."""

    __tablename__ = "api_response_cache"
    __table_args__ = (
        Index("ix_api_response_cache_expires_at", "expires_at"),
    )

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_method: Mapped[str] = mapped_column(Text)
    request_path: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_content_type: Mapped[str] = mapped_column(Text)
    response_body: Mapped[Any] = mapped_column(JSONB)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawPncpRecord(MainBase):
    """Versao imutavel de uma resposta publica do PNCP."""

    __tablename__ = "raw_pncp_records"
    __table_args__ = (
        UniqueConstraint(
            "source", "entity_type", "external_id", "payload_hash",
            name="uq_raw_pncp_record_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_hash: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class IngestionJob(MainBase):
    """Fila persistente. O worker sera adicionado em uma entrega posterior."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'FAILED', 'QUARANTINED')",
            name="ck_ingestion_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    scope_key: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    raw_record_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("raw_pncp_records.id", ondelete="RESTRICT")
    )
    priority: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page: Mapped[int | None] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    locked_by: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    replay_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    success_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class IngestionJobEvent(MainBase):
    """Auditoria append-only de tentativas, recuperação e replay de jobs."""

    __tablename__ = "ingestion_job_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ingestion_jobs.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpCursor(MainBase):
    __tablename__ = "pncp_cursors"
    flow: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_confirmed_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpContratacaoAtual(MainBase):
    __tablename__ = "pncp_contratacoes_atuais"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_hash: Mapped[str] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpOportunidade(MainBase):
    __tablename__ = "pncp_oportunidades"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    raw_record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"))
    payload_hash: Mapped[str] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    orgao: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    unidade: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    objeto: Mapped[str | None] = mapped_column(Text)
    data_publicacao: Mapped[date | None] = mapped_column(Date)
    ano_compra: Mapped[int | None] = mapped_column(Integer)
    modalidade: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    situacao: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    official_status: Mapped[str | None] = mapped_column(Text)
    derived_status: Mapped[str] = mapped_column(Text, server_default=text("'INDETERMINADO'"))
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    data_confidence: Mapped[str] = mapped_column(Text, server_default=text("'BAIXA'"))
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_rule_version: Mapped[str] = mapped_column(Text, server_default=text("'pncp-status-v1'"))
    prazo_proposta_final: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url_oficial: Mapped[str] = mapped_column(Text)
    data_quality_status: Mapped[str] = mapped_column(Text, server_default=text("'not_evaluated'"))
    dados_semanticos: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    normalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NlpProcurementSegment(MainBase):
    """Representação textual preparada, isolada das tabelas de ingestão PNCP."""

    __tablename__ = "nlp_procurement_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "external_id"],
            ["pncp_oportunidades.source", "pncp_oportunidades.external_id"],
            ondelete="CASCADE",
        ),
        Index("idx_nlp_procurement_segments_opportunity", "source", "external_id"),
        Index("idx_nlp_procurement_segments_text_hash", "text_hash"),
    )

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    segment_type: Mapped[str] = mapped_column(Text, primary_key=True)
    segment_key: Mapped[str] = mapped_column(Text, primary_key=True)
    source_field: Mapped[str] = mapped_column(Text)
    source_version_hash: Mapped[str] = mapped_column(Text)
    segment_version_hash: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    item_id: Mapped[int | None] = mapped_column(Integer)
    lot_id: Mapped[str | None] = mapped_column(Text)
    item_ids: Mapped[list[int]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    structured_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NlpProcurementEmbedding(MainBase):
    """Vetor imutavel por conteudo, origem e versao do modelo."""

    __tablename__ = "nlp_procurement_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "external_id", "segment_type", "segment_key"],
            ["nlp_procurement_segments.source", "nlp_procurement_segments.external_id",
             "nlp_procurement_segments.segment_type", "nlp_procurement_segments.segment_key"],
            ondelete="CASCADE",
        ),
        Index("idx_nlp_procurement_embeddings_model", "model_version"),
        Index(
            "idx_nlp_procurement_embeddings_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
            postgresql_with={"m": 12, "ef_construction": 48},
        ),
    )

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    segment_type: Mapped[str] = mapped_column(Text, primary_key=True)
    segment_key: Mapped[str] = mapped_column(Text, primary_key=True)
    text_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(VECTOR(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NlpMatchingBatch(MainBase):
    __tablename__ = "nlp_matching_batches"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')", name="ck_nlp_matching_batch_status"),
        CheckConstraint("total_companies >= 0 AND processed_companies >= 0 AND failed_companies >= 0 AND processed_companies + failed_companies <= total_companies", name="ck_nlp_matching_batch_counts"),
    )

    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payload_hash: Mapped[str] = mapped_column(Text)
    profiles: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'PENDING'"))
    total_companies: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    processed_companies: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_companies: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NlpMatchingResult(MainBase):
    __tablename__ = "nlp_matching_results"
    __table_args__ = (
        ForeignKeyConstraint(["batch_id"], ["nlp_matching_batches.batch_id"], ondelete="CASCADE"),
        Index("idx_nlp_matching_results_company", "company_id", "processed_at"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_nlp_matching_result_score"),
        CheckConstraint("eligibility IN ('ELIGIBLE', 'INELIGIBLE', 'NOT_CONFIRMED')", name="ck_nlp_matching_result_eligibility"),
    )

    batch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_version: Mapped[str] = mapped_column(Text, primary_key=True)
    procurement_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    matching_version: Mapped[str] = mapped_column(Text, primary_key=True)
    taxonomy_version: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    eligibility: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpEnriquecimento(MainBase):
    """Detalhes opcionais, versionados e independentes da oportunidade básica."""

    __tablename__ = "pncp_enriquecimentos"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cnpj_orgao: Mapped[str] = mapped_column(Text)
    ano_compra: Mapped[int] = mapped_column(Integer)
    sequencial_compra: Mapped[int] = mapped_column(Integer)
    source_version_hash: Mapped[str] = mapped_column(Text)
    detail_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    detail_hash: Mapped[str | None] = mapped_column(Text)
    items_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    items_hash: Mapped[str | None] = mapped_column(Text)
    documents_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    documents_hash: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpItem(MainBase):
    __tablename__ = "pncp_itens"
    __table_args__ = (ForeignKeyConstraint(["source", "external_id"], ["pncp_oportunidades.source", "pncp_oportunidades.external_id"]),)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_item: Mapped[int] = mapped_column(Integer, primary_key=True)
    numero_lote: Mapped[str | None] = mapped_column(Text)
    descricao: Mapped[str | None] = mapped_column(Text)
    quantidade: Mapped[str | None] = mapped_column(Text)
    unidade: Mapped[str | None] = mapped_column(Text)
    valor_unitario_estimado: Mapped[str | None] = mapped_column(Text)
    valor_total_estimado: Mapped[str | None] = mapped_column(Text)
    situacao: Mapped[str | None] = mapped_column(Text)
    criterio_julgamento: Mapped[str | None] = mapped_column(Text)
    resultado_status: Mapped[str] = mapped_column(Text, server_default="'NAO_CONSULTADO'")
    payload_hash: Mapped[str] = mapped_column(Text)
    revisao: Mapped[int] = mapped_column(Integer, server_default="1")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpResultadoItem(MainBase):
    __tablename__ = "pncp_resultados_itens"
    __table_args__ = (ForeignKeyConstraint(["source", "external_id", "numero_item"], ["pncp_itens.source", "pncp_itens.external_id", "pncp_itens.numero_item"]),)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    numero_item: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequencial_resultado: Mapped[int] = mapped_column(Integer, primary_key=True)
    fornecedor: Mapped[str | None] = mapped_column(Text)
    cnpj_fornecedor: Mapped[str | None] = mapped_column(Text)
    valor_unitario_homologado: Mapped[str | None] = mapped_column(Text)
    quantidade_homologada: Mapped[str | None] = mapped_column(Text)
    situacao: Mapped[str | None] = mapped_column(Text)
    data_resultado: Mapped[date | None] = mapped_column(Date)
    disponibilidade: Mapped[str] = mapped_column(Text, server_default="'ATUAL'")
    payload_hash: Mapped[str] = mapped_column(Text)
    revisao: Mapped[int] = mapped_column(Integer, server_default="1")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpDocumento(MainBase):
    __tablename__ = "pncp_documentos"
    __table_args__ = (ForeignKeyConstraint(["source", "external_id"], ["pncp_oportunidades.source", "pncp_oportunidades.external_id"]),)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sequencial_documento: Mapped[int] = mapped_column(Integer, primary_key=True)
    titulo: Mapped[str | None] = mapped_column(Text)
    tipo: Mapped[str | None] = mapped_column(Text)
    url_oficial: Mapped[str] = mapped_column(Text)
    publicado_em: Mapped[date | None] = mapped_column(Date)
    payload_hash: Mapped[str] = mapped_column(Text)
    revisao: Mapped[int] = mapped_column(Integer, server_default="1")
    disponibilidade: Mapped[str] = mapped_column(Text, server_default="'DISPONIVEL'")
    ausencias_consecutivas: Mapped[int] = mapped_column(Integer, server_default="0")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpRecursoVersao(MainBase):
    __tablename__ = "pncp_recurso_versoes"
    __table_args__ = (UniqueConstraint("source", "external_id", "resource_type", "resource_key", "revisao", name="uq_pncp_recurso_versao"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_key: Mapped[str] = mapped_column(Text)
    revisao: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpEvento(MainBase):
    __tablename__ = "pncp_eventos"
    __table_args__ = (UniqueConstraint("event_key", name="uq_pncp_eventos_event_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpMatching(MainBase):
    __tablename__ = "pncp_matchings"
    __table_args__ = (UniqueConstraint("source", "external_id", "profile_key", "rule_version", name="uq_pncp_matching_rule"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    profile_key: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    compatible: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpTaxonomiaCategoria(MainBase):
    __tablename__ = "pncp_taxonomia_categorias"
    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    nome: Mapped[str] = mapped_column(Text)
    descricao: Mapped[str] = mapped_column(Text)


class PncpTaxonomiaCnae(MainBase):
    __tablename__ = "pncp_taxonomia_cnaes"
    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descricao: Mapped[str] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(Text)


class PncpMapaRevisao(MainBase):
    __tablename__ = "pncp_mapa_revisoes"
    __table_args__ = (CheckConstraint("estado IN ('RASCUNHO', 'ATIVA', 'SUBSTITUIDA')", name="ck_pncp_mapa_estado"),)
    versao: Mapped[str] = mapped_column(Text, primary_key=True)
    estado: Mapped[str] = mapped_column(Text, server_default=text("'RASCUNHO'"))
    evidencia: Mapped[str] = mapped_column(Text)
    criado_por: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    ativado_por: Mapped[str | None] = mapped_column(Text)
    ativado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PncpMapaCnaeCategoria(MainBase):
    __tablename__ = "pncp_mapa_cnae_categoria"
    __table_args__ = (
        UniqueConstraint("versao", "cnae_codigo", "categoria_slug", name="uq_pncp_mapa_relacao"),
        CheckConstraint("tipo IN ('DIRETA', 'RELACIONADA', 'CONDICIONAL')", name="ck_pncp_mapa_tipo"),
        CheckConstraint("confianca >= 0 AND confianca <= 1", name="ck_pncp_mapa_confianca"),
        CheckConstraint("tipo != 'CONDICIONAL' OR condicao IS NOT NULL", name="ck_pncp_mapa_condicao"),
        ForeignKeyConstraint(["versao"], ["pncp_mapa_revisoes.versao"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["cnae_codigo"], ["pncp_taxonomia_cnaes.codigo"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["categoria_slug"], ["pncp_taxonomia_categorias.slug"], ondelete="RESTRICT"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versao: Mapped[str] = mapped_column(Text)
    cnae_codigo: Mapped[str] = mapped_column(Text)
    categoria_slug: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    confianca: Mapped[float] = mapped_column(Float)
    ativo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    condicao: Mapped[str | None] = mapped_column(Text)
    evidencias: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    revisado_por: Mapped[str] = mapped_column(Text)
    revisado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpQuarentena(MainBase):
    __tablename__ = "pncp_quarentena"
    raw_record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"), primary_key=True)
    motivo: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpSyncCursor(MainBase):
    __tablename__ = "pncp_sync_cursors"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, primary_key=True)
    flow: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, primary_key=True)
    last_confirmed_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'IDLE'"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class PncpSourceControl(MainBase):
    """Estado compartilhado de proteção da fonte entre workers PNCP."""

    __tablename__ = "pncp_source_controls"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    circuit_state: Mapped[str] = mapped_column(Text, server_default=text("'CLOSED'"))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    half_open_in_flight: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    concurrency_limit: Mapped[int] = mapped_column(Integer, server_default=text("2"))
    active_requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval_milliseconds: Mapped[int] = mapped_column(Integer, server_default=text("500"))
    successes_since_reduction: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class TceDespesaIngestionRun(MainBase):
    __tablename__ = "tce_despesa_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('EM_VALIDACAO', 'PUBLICADO', 'SUBSTITUIDO', 'REJEITADO')",
            name="ck_tce_despesa_runs_status",
        ),
        CheckConstraint("quantidade_empenhos >= 0", name="ck_tce_despesa_runs_qtd_empenhos"),
        CheckConstraint("quantidade_anulacoes >= 0", name="ck_tce_despesa_runs_qtd_anulacoes"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codigo_municipio_tce: Mapped[str] = mapped_column(Text)
    competencia: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    substituido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quantidade_empenhos: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_anulacoes: Mapped[int] = mapped_column(Integer, default=0)
    problemas: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
    )
    erro: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    empenhos: Mapped[list[TceEmpenho]] = relationship(
        back_populates="lote",
        cascade="all, delete-orphan",
    )
    anulacoes: Mapped[list[TceAnulacaoEmpenho]] = relationship(
        back_populates="lote",
        cascade="all, delete-orphan",
    )


class TceEmpenho(MainBase):
    __tablename__ = "tce_empenhos"
    __table_args__ = (
        UniqueConstraint("run_id", "chave_empenho", name="uq_tce_empenhos_run_chave"),
        CheckConstraint(
            "valor_empenhado_centavos >= 0",
            name="ck_tce_empenhos_valor_nao_negativo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tce_despesa_ingestion_runs.id", ondelete="CASCADE"),
    )
    chave_empenho: Mapped[str] = mapped_column(Text)
    exercicio_orcamento: Mapped[int] = mapped_column(Integer)
    codigo_orgao: Mapped[str] = mapped_column(Text)
    codigo_unidade_orcamentaria: Mapped[str] = mapped_column(Text)
    data_empenho: Mapped[date] = mapped_column(Date)
    numero_empenho: Mapped[str] = mapped_column(Text)
    codigo_natureza_despesa: Mapped[str] = mapped_column(Text)
    codigo_elemento_despesa: Mapped[str] = mapped_column(Text)
    natureza_considerada: Mapped[bool] = mapped_column(Boolean)
    valor_empenhado_centavos: Mapped[int] = mapped_column(BigInteger)
    tipo_documento_fornecedor: Mapped[str] = mapped_column(Text)
    documento_fornecedor: Mapped[str | None] = mapped_column(Text)
    cnpj_fornecedor: Mapped[str | None] = mapped_column(Text)
    cpf_fornecedor: Mapped[str | None] = mapped_column(Text)
    nome_fornecedor: Mapped[str | None] = mapped_column(Text)
    municipio_fornecedor_informado: Mapped[str | None] = mapped_column(Text)
    uf_fornecedor_informada: Mapped[str | None] = mapped_column(Text)
    estado_empenho: Mapped[str | None] = mapped_column(Text)
    numero_nota_anulacao_informado: Mapped[str | None] = mapped_column(Text)
    numero_empenho_substituto: Mapped[str | None] = mapped_column(Text)
    numero_contrato: Mapped[str | None] = mapped_column(Text)
    numero_licitacao: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    lote: Mapped[TceDespesaIngestionRun] = relationship(back_populates="empenhos")


class TceAnulacaoEmpenho(MainBase):
    __tablename__ = "tce_anulacoes_empenhos"
    __table_args__ = (
        UniqueConstraint("run_id", "chave_anulacao", name="uq_tce_anulacoes_run_chave"),
        CheckConstraint(
            "valor_anulacao_centavos >= 0",
            name="ck_tce_anulacoes_valor_nao_negativo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tce_despesa_ingestion_runs.id", ondelete="CASCADE"),
    )
    chave_anulacao: Mapped[str] = mapped_column(Text)
    chave_empenho: Mapped[str] = mapped_column(Text)
    data_empenho: Mapped[date] = mapped_column(Date)
    numero_empenho: Mapped[str] = mapped_column(Text)
    numero_anulacao: Mapped[str] = mapped_column(Text)
    data_anulacao: Mapped[date] = mapped_column(Date)
    modalidade_anulacao: Mapped[str | None] = mapped_column(Text)
    descricao_anulacao: Mapped[str | None] = mapped_column(Text)
    valor_anulacao_centavos: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    lote: Mapped[TceDespesaIngestionRun] = relationship(back_populates="anulacoes")


Index("idx_ibge_municipios_uf_nome", IbgeMunicipio.uf, IbgeMunicipio.nome)
Index("idx_fornecedores_cache_expira_em", FornecedorCache.expira_em)
Index("idx_raw_pncp_records_entidade_identificador", RawPncpRecord.source, RawPncpRecord.entity_type, RawPncpRecord.external_id)
Index(
    "uq_ingestion_jobs_dedupe_ativo",
    IngestionJob.dedupe_key,
    unique=True,
    postgresql_where=text("status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
)
Index("idx_ingestion_jobs_agendamento", IngestionJob.status, IngestionJob.next_run_at, IngestionJob.priority)
Index("idx_ingestion_jobs_lease", IngestionJob.status, IngestionJob.lease_until)
Index("idx_ingestion_job_events_job_created", IngestionJobEvent.job_id, IngestionJobEvent.created_at)
Index("idx_pncp_contratacoes_atuais_atualizacao", PncpContratacaoAtual.source_updated_at)
Index("idx_pncp_oportunidades_prazo", PncpOportunidade.prazo_proposta_final)
Index("idx_pncp_oportunidades_derived_status", PncpOportunidade.derived_status, PncpOportunidade.prazo_proposta_final)
Index("idx_pncp_oportunidades_data_publicacao", PncpOportunidade.data_publicacao)
Index("idx_pncp_oportunidades_ano_compra", PncpOportunidade.ano_compra)
Index("idx_pncp_enriquecimentos_expiracao", PncpEnriquecimento.expires_at)
Index("idx_pncp_eventos_contratacao", PncpEvento.source, PncpEvento.external_id)
Index("idx_pncp_matchings_profile", PncpMatching.profile_key, PncpMatching.compatible)
Index("idx_pncp_mapa_snapshot", PncpMapaCnaeCategoria.versao, PncpMapaCnaeCategoria.ativo, PncpMapaCnaeCategoria.cnae_codigo)
Index(
    "uq_tce_despesa_run_publicado_competencia",
    TceDespesaIngestionRun.codigo_municipio_tce,
    TceDespesaIngestionRun.competencia,
    unique=True,
    postgresql_where=text("status = 'PUBLICADO'"),
)
Index(
    "idx_tce_despesa_runs_status_expiracao",
    TceDespesaIngestionRun.status,
    TceDespesaIngestionRun.expira_em,
)
Index("idx_tce_empenhos_run", TceEmpenho.run_id)
Index("idx_tce_empenhos_chave", TceEmpenho.chave_empenho)
Index(
    "idx_tce_empenhos_analitico",
    TceEmpenho.natureza_considerada,
    TceEmpenho.data_empenho,
    TceEmpenho.codigo_elemento_despesa,
)
Index(
    "idx_tce_empenhos_fornecedor_data",
    TceEmpenho.tipo_documento_fornecedor,
    TceEmpenho.documento_fornecedor,
    TceEmpenho.data_empenho,
    postgresql_where=text("documento_fornecedor IS NOT NULL"),
)
Index("idx_tce_anulacoes_run", TceAnulacaoEmpenho.run_id)
Index("idx_tce_anulacoes_chave_empenho", TceAnulacaoEmpenho.chave_empenho)


__all__ = [
    "ApiResponseCache",
    "FornecedorCache",
    "FornecedorMe",
    "IngestionJob",
    "IngestionJobEvent",
    "IbgeMunicipio",
    "RawPncpRecord",
    "PncpCursor",
    "PncpContratacaoAtual",
    "PncpOportunidade",
    "PncpEnriquecimento",
    "PncpItem",
    "PncpResultadoItem",
    "PncpDocumento",
    "PncpRecursoVersao",
    "PncpEvento",
    "PncpMatching",
    "PncpTaxonomiaCategoria",
    "PncpTaxonomiaCnae",
    "PncpMapaRevisao",
    "PncpMapaCnaeCategoria",
    "PncpQuarentena",
    "PncpSyncCursor",
    "PncpSourceControl",
    "TceAnulacaoEmpenho",
    "TceDespesaIngestionRun",
    "TceEmpenho",
    "TceMunicipio",
]

# Registra as tabelas de observacoes mensais no metadata Alembic.
from app.core.porte_models import PortePublicacao, PorteObservacao  # noqa: E402,F401


class PncpAvaliacaoQualidade(MainBase):
    """Uma evidencia por RAW e versao da regra; replay nao duplica metricas."""

    __tablename__ = "pncp_avaliacoes_qualidade"
    raw_record_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("raw_pncp_records.id", ondelete="RESTRICT"), primary_key=True)
    rule_version: Mapped[str] = mapped_column(Text, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, index=True)
    run_id: Mapped[str | None] = mapped_column(Text, index=True)
    data_quality_status: Mapped[str] = mapped_column(Text)
    decisao: Mapped[str] = mapped_column(Text, server_default=text("'AVALIADO'"))
    campos: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tentativas: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    avaliado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
