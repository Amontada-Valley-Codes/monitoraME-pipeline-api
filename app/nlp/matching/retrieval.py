"""Busca vetorial simples sobre matrizes de embeddings E5 normalizadas."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.nlp.embeddings.model import DIMENSAO_EMBEDDING


@dataclass(frozen=True)
class RetrievalHit:
    index: int
    score: float


def buscar_top_k(
    query_vector: list[float], passage_vectors: list[list[float]], *, k: int = 10
) -> list[RetrievalHit]:
    """Retorna índices ordenados por cosine similarity (vetores L2-normalizados)."""
    if k < 1:
        raise ValueError("k deve ser maior que zero")
    if len(query_vector) != DIMENSAO_EMBEDDING:
        raise ValueError(f"query_vector deve ter {DIMENSAO_EMBEDDING} dimensões")
    if not passage_vectors:
        return []
    matriz = np.asarray(passage_vectors, dtype=np.float32)
    consulta = np.asarray(query_vector, dtype=np.float32)
    if matriz.ndim != 2 or matriz.shape[1] != DIMENSAO_EMBEDDING:
        raise ValueError(f"passage_vectors deve ter dimensão N x {DIMENSAO_EMBEDDING}")
    if not np.isfinite(matriz).all() or not np.isfinite(consulta).all():
        raise ValueError("vetores devem conter apenas valores finitos")
    norma_consulta = float(np.linalg.norm(consulta))
    normas_passagens = np.linalg.norm(matriz, axis=1)
    if not np.isclose(norma_consulta, 1.0, atol=1e-3) or not np.allclose(
        normas_passagens, 1.0, atol=1e-3
    ):
        raise ValueError("vetores devem estar normalizados em norma L2")
    scores = matriz @ consulta
    indices = np.argsort(-scores, kind="stable")[: min(k, len(scores))]
    return [RetrievalHit(int(i), float(scores[i])) for i in indices]
