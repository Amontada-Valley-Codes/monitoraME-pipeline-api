"""Serviços de aplicação para batches de matching recebidos por HTTP."""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.core import orm
from app.core.models import NlpMatchingBatch
from app.nlp.matching.schemas import BatchAccepted, BatchStatus
from app.nlp.profiles.schemas import MatchingBatchRequest
from app.nlp.repositories.matching import enfileirar_batch_session, hash_payload


def enfileirar_batch_http(request: MatchingBatchRequest) -> BatchAccepted:
    """Persiste batch e job atomicamente, tratando repetição concorrente."""
    payload_hash = hash_payload(request.companies)
    try:
        with orm.main_session() as session:
            batch, _ = enfileirar_batch_session(
                session, batch_id=str(request.batch_id), profiles=request.companies,
            )
            return BatchAccepted(
                batch_id=request.batch_id,
                status=BatchStatus(batch.status.lower()),
                accepted_companies=batch.total_companies,
            )
    except IntegrityError:
        # Duas submissões simultâneas com o mesmo UUID podem disputar o INSERT.
        # Depois do rollback, aceite a repetição idêntica e rejeite payload diferente.
        with orm.main_session() as session:
            existing = session.get(NlpMatchingBatch, str(request.batch_id))
            if existing is None:
                raise
            if existing.payload_hash != payload_hash:
                raise ValueError("batch_id ja existe associado a outro payload")
            return BatchAccepted(
                batch_id=request.batch_id,
                status=BatchStatus(existing.status.lower()),
                accepted_companies=existing.total_companies,
            )


__all__ = ["enfileirar_batch_http"]
