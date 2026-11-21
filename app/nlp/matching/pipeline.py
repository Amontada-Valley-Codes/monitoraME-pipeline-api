"""Execução local de matching sobre candidatos Top-K e PNCP normalizado."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any, Sequence

from sqlalchemy import select, tuple_

from app.core import orm
from app.core.models import (
    NlpMatchingBatch, PncpItem, PncpMapaCnaeCategoria, PncpMapaRevisao,
    NlpProcurementSegment, PncpOportunidade, PncpTaxonomiaCnae,
)
from app.nlp.embeddings.model import E5EmbeddingModel
from app.nlp.matching.categories import CATEGORY_RULE_VERSION, classificar_categorias_edital
from app.nlp.matching.engine import calcular_matching
from app.nlp.matching.postgres_retrieval import buscar_contratacoes_top_k
from app.nlp.matching.schemas import MatchingResult, ProcurementProfile
from app.nlp.matching.scoring import TaxonomyAssociation
from app.nlp.profiles.builder import construir_perfil_semantico
from app.nlp.profiles.schemas import CompanyProfile
from app.nlp.repositories.matching import (
    hash_payload, listar_resultados_batch_session, listar_resultados_session,
    consultar_batch_session, marcar_batch_falha_session, preparar_batch_reexecucao_session,
    registrar_batch_session, salvar_resultados_session,
)
from app.pncp.jobs import concluir_job_session, renovar_lease_session

logger = logging.getLogger(__name__)
SEGMENT_QUERY_CHUNK_SIZE = 200


def _carregar_segmentos_evidencia(session: Any, keys: list[tuple[str, str, str, str]]) -> dict[tuple[str, str, str, str], NlpProcurementSegment]:
    """Evita uma única expressão IN extensa para batches grandes."""
    unique_keys = list(dict.fromkeys(keys))
    stored: dict[tuple[str, str, str, str], NlpProcurementSegment] = {}
    for start in range(0, len(unique_keys), SEGMENT_QUERY_CHUNK_SIZE):
        chunk = unique_keys[start:start + SEGMENT_QUERY_CHUNK_SIZE]
        for row in session.scalars(select(NlpProcurementSegment).where(
            tuple_(NlpProcurementSegment.source, NlpProcurementSegment.external_id,
                   NlpProcurementSegment.segment_type, NlpProcurementSegment.segment_key).in_(chunk)
        )):
            stored[(row.source, row.external_id, row.segment_type, row.segment_key)] = row
    return stored


def _renovar_lease_matching(job: Any | None) -> None:
    if job is None:
        return
    with orm.main_session() as session:
        if not renovar_lease_session(session, job_id=job.id, lease_token=job.lease_token, lease_seconds=7200):
            raise RuntimeError("lease de matching expirou durante o processamento")


def _executar_matching_local(
    profiles: Sequence[CompanyProfile],
    *,
    batch_id: str,
    model: E5EmbeddingModel,
    k: int | None = None,
    as_of: datetime | None = None,
    leased_job: Any | None = None,
) -> tuple[list[MatchingResult], bool]:
    """Executa e persiste um batch local; repetição idêntica é idempotente."""
    if not profiles:
        raise ValueError("batch deve conter ao menos um perfil")
    top_k = k if k is not None else int(os.getenv("NLP_RETRIEVAL_TOP_K", "20"))
    if top_k < 1:
        raise ValueError("k deve ser maior que zero")
    instant = as_of or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("as_of deve incluir timezone")
    payload_hash = hash_payload(profiles)
    with orm.main_session() as session:
        existing = session.get(NlpMatchingBatch, batch_id)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ValueError("batch_id já existe associado a outro payload")
            if existing.status == "COMPLETED":
                previous, _ = listar_resultados_batch_session(session, batch_id=batch_id)
                return [MatchingResult.model_validate(item) for item in previous], False
        else:
            registrar_batch_session(session, batch_id=batch_id, profiles=profiles)
        preparar_batch_reexecucao_session(session, batch_id=batch_id)

    model.load()
    results: list[MatchingResult] = []
    failed_profiles = 0
    with orm.main_session() as session:
        cnae_codes = {cnae.code for profile in profiles for cnae in profile.cnaes if not cnae.description}
        catalog = dict(session.execute(
            select(PncpTaxonomiaCnae.codigo, PncpTaxonomiaCnae.descricao)
            .where(PncpTaxonomiaCnae.codigo.in_(cnae_codes))
        ).all()) if cnae_codes else {}
    semantic_profiles = {}
    for profile in profiles:
        semantic_profile = construir_perfil_semantico(profile, model, descricoes_catalogo=catalog)
        semantic_profiles[str(profile.company_id)] = semantic_profile
        _renovar_lease_matching(leased_job)
        logger.info("Vetor de consulta do perfil gerado", extra={
            "batch_id": batch_id,
            "company_id": str(profile.company_id),
            "cnaes_recebidos": len(profile.cnaes),
            "consultas_semanticas_geradas": len(semantic_profile.consultas),
            "cnaes_sem_descricao": len(semantic_profile.cnaes_sem_descricao),
            "model_version": semantic_profile.model_version,
        })
        if not semantic_profile.consultas:
            failed_profiles += 1
            logger.warning("Perfil sem CNAE com descricao utilizavel", extra={
                "company_id": str(profile.company_id),
                "missing_cnaes": list(semantic_profile.cnaes_sem_descricao),
            })

    with orm.main_session() as session:
        map_row = session.execute(
            select(PncpMapaRevisao.versao)
            .where(PncpMapaRevisao.estado == "ATIVA")
            .limit(1)
        ).first()
        taxonomy_version = map_row[0] if map_row else "taxonomy-unavailable"
        taxonomy_rows = session.execute(
            select(PncpMapaCnaeCategoria.cnae_codigo, PncpMapaCnaeCategoria.categoria_slug,
                   PncpMapaCnaeCategoria.tipo, PncpMapaCnaeCategoria.confianca,
                   PncpMapaCnaeCategoria.condicao)
            .where(PncpMapaCnaeCategoria.versao == taxonomy_version,
                   PncpMapaCnaeCategoria.ativo.is_(True))
        ).mappings()
        associations: dict[str, list[TaxonomyAssociation]] = {}
        for row in taxonomy_rows:
            associations.setdefault(row["cnae_codigo"], []).append(TaxonomyAssociation(
                category=row["categoria_slug"], relation_type=row["tipo"],
                confidence=float(row["confianca"]), condition=row["condicao"],
            ))
        active_categories = {association.category for values in associations.values() for association in values}

        for profile in profiles:
            semantic_profile = semantic_profiles[str(profile.company_id)]
            if not semantic_profile.consultas:
                continue
            resultados_antes = len(results)
            candidates = buscar_contratacoes_top_k(
                session, semantic_profile.consultas,
                model_version=semantic_profile.model_version, k=top_k,
                derived_statuses=("ABERTA", "ENCERRANDO_EM_BREVE"),
            )
            _renovar_lease_matching(leased_job)
            candidate_keys = [(item.source, item.procurement_id) for item in candidates]
            opportunities = {
                (row.source, row.external_id): row
                for row in session.scalars(
                    select(PncpOportunidade).where(
                        tuple_(PncpOportunidade.source, PncpOportunidade.external_id).in_(candidate_keys)
                    )
                )
            } if candidate_keys else {}
            items_by_opportunity: dict[tuple[str, str], list[str]] = {}
            if candidate_keys:
                item_rows = session.execute(
                    select(PncpItem.source, PncpItem.external_id, PncpItem.descricao)
                    .where(
                        tuple_(PncpItem.source, PncpItem.external_id).in_(candidate_keys),
                        PncpItem.descricao.is_not(None),
                    )
                    .order_by(PncpItem.source, PncpItem.external_id, PncpItem.numero_item)
                )
                for source, external_id, description in item_rows:
                    items_by_opportunity.setdefault((source, external_id), []).append(description)
            segment_keys = [
                (item.source, item.procurement_id, segment["segment_type"], segment["segment_key"])
                for item in candidates for segment in item.segments[:10]
            ]
            stored_segments = _carregar_segmentos_evidencia(session, segment_keys)
            for candidate in candidates:
                key = (candidate.source, candidate.procurement_id)
                opportunity = opportunities.get(key)
                if opportunity is None:
                    continue
                evidence_segments = []
                cnae_descriptions = {
                    cnae.code: cnae.description or catalog.get(cnae.code)
                    for cnae in profile.cnaes
                }
                for segment in candidate.segments[:10]:
                    stored_segment = stored_segments.get((
                        candidate.source, candidate.procurement_id,
                        segment["segment_type"], segment["segment_key"],
                    ))
                    evidence_segments.append({
                        **segment,
                        "cnae_description": cnae_descriptions.get(segment["cnae"]),
                        "snippet": stored_segment.normalized_text[:220] if stored_segment and stored_segment.normalized_text else None,
                    })
                status = opportunity.derived_status
                status_upper = (status or "").upper()
                official_upper = (opportunity.official_status or "").upper()
                modality_data = (opportunity.dados_semanticos or {}).get("modalidade_codigo")
                modality_code = modality_data.get("valor") if isinstance(modality_data, dict) else modality_data
                procurement = ProcurementProfile(
                    procurement_id=f"{candidate.source}:{candidate.procurement_id}",
                    source=candidate.source, external_id=candidate.procurement_id,
                    object=opportunity.objeto,
                    complementary_text=(opportunity.dados_semanticos or {}).get("informacao_complementar"),
                    items=items_by_opportunity.get(key, []),
                    categories=classificar_categorias_edital(
                        opportunity.objeto, items_by_opportunity.get(key, []),
                        categorias_ativas=active_categories,
                    ),
                    modality_code=str(modality_code) if modality_code is not None else None,
                    status=status, deadline=opportunity.prazo_proposta_final,
                    revoked=True if "REVOG" in status_upper or "REVOG" in official_upper else (False if opportunity.official_status is not None else None),
                    annulled=True if "ANUL" in status_upper or "ANUL" in official_upper else (False if opportunity.official_status is not None else None),
                    suspended=True if "SUSPEN" in status_upper or "SUSPEN" in official_upper else (False if opportunity.official_status is not None else None),
                    url=opportunity.url_oficial, version=opportunity.payload_hash,
                )
                matching = calcular_matching(
                    profile, procurement, taxonomy_version=taxonomy_version,
                    cnae_categories=associations,
                    semantic_score=max(0.0, min(1.0, candidate.score)),
                    semantic_object_score=max((float(s["score"]) for s in candidate.segments if s["segment_type"] == "OBJECT"), default=0.0),
                    semantic_complementary_score=max((float(s["score"]) for s in candidate.segments if s["segment_type"] == "COMPLEMENTARY" and s["segment_key"] == "complementary:informacao_complementar"), default=0.0),
                    semantic_items_score=max((float(s["score"]) for s in candidate.segments if s["segment_type"] in {"ITEM", "LOT"}), default=0.0),
                    model_version=semantic_profile.model_version, as_of=instant,
                    retrieval_evidence=evidence_segments,
                    additional_warnings=(
                        ["PROFILE_CNAE_DESCRIPTION_INCOMPLETE"]
                        if semantic_profile.cnaes_sem_descricao else []
                    ) + (["OPPORTUNITY_CATEGORY_UNCONFIRMED"] if not procurement.categories else []),
                )
                results.append(matching)
            logger.info("Perfil avaliado no matching NLP", extra={
                "batch_id": batch_id,
                "company_id": str(profile.company_id),
                "candidatos_recuperados": len(candidates),
                "top_k_configurado": top_k,
                "category_rule_version": CATEGORY_RULE_VERSION,
                "oportunidades_disponiveis": len(opportunities),
                "resultados_gerados": len(results) - resultados_antes,
                "model_version": semantic_profile.model_version,
            })

    with orm.main_session() as session:
        if leased_job is not None and not concluir_job_session(
            session, job_id=leased_job.id, lease_token=leased_job.lease_token,
        ):
            raise RuntimeError("lease de matching expirou antes de persistir resultados")
        salvar_resultados_session(
            session, batch_id=batch_id, results=results,
            total_companies=len(profiles), failed_companies=failed_profiles,
        )
    return results, True


def consultar_resultados_empresa(company_id: str, *, limit: int = 100) -> list[dict[str, object]]:
    with orm.main_session() as session:
        return listar_resultados_session(session, company_id=company_id, limit=limit)


def consultar_status_batch(batch_id: str) -> dict[str, object] | None:
    with orm.main_session() as session:
        return consultar_batch_session(session, batch_id=batch_id)


def consultar_resultados_batch(
    batch_id: str, *, limit: int = 100, cursor: str | None = None,
) -> dict[str, object] | None:
    with orm.main_session() as session:
        status = consultar_batch_session(session, batch_id=batch_id)
        if status is None:
            return None
        results, next_cursor = listar_resultados_batch_session(
            session, batch_id=batch_id, limit=limit, cursor=cursor, high_only=True,
        )
        return {
            "batch_id": batch_id,
            "schema_version": "matching.v1",
            "status": status["status"],
            "results": results,
            "next_cursor": next_cursor,
        }


def processar_batch_enfileirado(job: Any, *, model: E5EmbeddingModel, k: int | None = None) -> tuple[list[MatchingResult], bool]:
    batch_id = job.scope_key
    with orm.main_session() as session:
        batch = session.get(NlpMatchingBatch, batch_id)
        if batch is None or not batch.profiles:
            raise ValueError("batch enfileirado sem snapshot de perfis")
        profiles = [CompanyProfile.model_validate(item) for item in batch.profiles]
        batch_instant = batch.created_at
    logger.info("Snapshot de perfis carregado para matching NLP", extra={
        "batch_id": batch_id,
        "job_id": job.id,
        "perfis_recebidos": len(profiles),
        "model_version": model.model_version,
    })
    results, created = executar_matching_local(
        profiles, batch_id=batch_id, model=model, k=k, as_of=batch_instant,
        leased_job=job,
    )
    if not created:
        with orm.main_session() as session:
            if not concluir_job_session(session, job_id=job.id, lease_token=job.lease_token):
                raise RuntimeError("lease de matching expirou antes de concluir batch já processado")
    logger.info("Batch de matching NLP processado", extra={
        "batch_id": batch_id,
        "job_id": job.id,
        "perfis_recebidos": len(profiles),
        "resultados_persistidos": len(results),
        "novo_processamento": created,
        "model_version": model.model_version,
    })
    return results, created


def executar_matching_local(
    profiles: Sequence[CompanyProfile],
    *,
    batch_id: str,
    model: E5EmbeddingModel,
    k: int | None = None,
    as_of: datetime | None = None,
    leased_job: Any | None = None,
) -> tuple[list[MatchingResult], bool]:
    try:
        return _executar_matching_local(
            profiles, batch_id=batch_id, model=model, k=k, as_of=as_of,
            leased_job=leased_job,
        )
    except Exception:
        if leased_job is None:
            try:
                with orm.main_session() as session:
                    marcar_batch_falha_session(
                        session, batch_id=batch_id, payload_hash=hash_payload(profiles),
                    )
            except Exception:
                logger.exception("Falha ao atualizar estado de erro do batch NLP", extra={"batch_id": batch_id})
        raise
