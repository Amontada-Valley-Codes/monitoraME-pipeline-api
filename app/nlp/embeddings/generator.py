"""Geração em lote para perfis de empresa e segmentos PNCP."""
from __future__ import annotations

from app.nlp.embeddings.model import E5EmbeddingModel, EmbeddingBatch


def gerar_embeddings_perfis(
    model: E5EmbeddingModel, textos: list[str]
) -> EmbeddingBatch:
    """Perfis funcionam como consultas em recuperação assimétrica E5."""
    return model.encode(textos, input_type="query")


def gerar_embeddings_segmentos(
    model: E5EmbeddingModel, textos: list[str]
) -> EmbeddingBatch:
    """Textos de objeto/item/lote funcionam como passagens recuperáveis."""
    return model.encode(textos, input_type="passage")
