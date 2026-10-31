"""Métricas locais e auditáveis do pipeline PNCP; nenhuma chamada HTTP externa."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import orm


def metricas_session(session: Session) -> dict[str, object]:
    jobs = session.execute(text("""
        SELECT status, count(*)::int AS total, coalesce(sum(attempts), 0)::int AS tentativas
        FROM ingestion_jobs GROUP BY status
    """)).mappings().all()
    fluxos = session.execute(text("""
        SELECT split_part(scope_key, ':', 1) AS fluxo, max(updated_at) FILTER (WHERE status = 'SUCCEEDED') AS last_success_at,
               extract(epoch FROM now() - max(updated_at) FILTER (WHERE status = 'SUCCEEDED')) AS lag_seconds,
               count(*) FILTER (WHERE status IN ('PENDING', 'RETRY_WAIT'))::int AS pendentes
        FROM ingestion_jobs WHERE source = 'PNCP' GROUP BY split_part(scope_key, ':', 1)
    """)).mappings().all()
    totais = session.execute(text("""
        SELECT
          (SELECT count(*)::int FROM raw_pncp_records) AS raw_total,
          (SELECT count(*)::int FROM ingestion_jobs WHERE job_type = 'normalize' AND status IN ('PENDING','RUNNING','RETRY_WAIT')) AS raw_backlog,
          (SELECT count(*)::int FROM ingestion_jobs WHERE job_type = 'enrich' AND status IN ('PENDING','RUNNING','RETRY_WAIT')) AS enrichment_backlog,
          (SELECT count(*)::int FROM ingestion_job_events WHERE event_type = 'LEASE_EXPIRED') AS leases_expirados,
          (SELECT count(*)::int FROM pncp_eventos) AS eventos_gerados
    """)).mappings().one()
    cobertura = session.execute(text("""
        SELECT
          (SELECT count(*)::int FROM pncp_oportunidades) AS oportunidades,
          (SELECT count(DISTINCT (source, external_id))::int FROM pncp_itens) AS oportunidades_com_itens,
          (SELECT count(*)::int FROM pncp_itens) AS itens,
          (SELECT count(*)::int FROM pncp_itens WHERE numero_lote IS NOT NULL) AS itens_com_lote,
          (SELECT count(DISTINCT (source, external_id, numero_lote))::int FROM pncp_itens WHERE numero_lote IS NOT NULL) AS lotes_identificados,
          (SELECT count(*)::int FROM pncp_itens WHERE resultado_status = 'AUSENTE') AS itens_resultado_ausente,
          (SELECT count(*)::int FROM pncp_itens WHERE resultado_status = 'PARCIAL') AS itens_resultado_parcial,
          (SELECT count(*)::int FROM pncp_itens WHERE resultado_status = 'FINAL') AS itens_resultado_final,
          (SELECT count(*)::int FROM pncp_resultados_itens) AS resultados,
          (SELECT count(*)::int FROM pncp_documentos) AS documentos,
          (SELECT count(*)::int FROM pncp_documentos WHERE disponibilidade = 'TEMPORARIAMENTE_INDISPONIVEL') AS documentos_temporarios,
          (SELECT count(*)::int FROM pncp_documentos WHERE disponibilidade = 'REMOVIDO') AS documentos_removidos,
          (SELECT count(*)::int FROM pncp_recurso_versoes) AS versoes
    """)).mappings().one()
    return {"jobs": {r["status"]: {"total": r["total"], "tentativas": r["tentativas"]} for r in jobs}, "fluxos": [dict(r) for r in fluxos], "totais": dict(totais), "cobertura_participacao": dict(cobertura)}


def metricas() -> dict[str, object]:
    with orm.main_session() as session:
        return metricas_session(session)
