"""Adaptador CPU-first para o modelo multilingual E5 small."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

TipoTexto = Literal["query", "passage"]
MODEL_ID_PADRAO = "intfloat/multilingual-e5-small"
MAX_TOKENS = 512
DIMENSAO_EMBEDDING = 384


@dataclass(frozen=True)
class EmbeddingBatch:
    """Vetores e metadados suficientes para persistir/reproduzir a geração."""

    model_version: str
    input_type: TipoTexto
    vectors: list[list[float]]


class E5EmbeddingModel:
    """Gera vetores normalizados; o import do backend pesado ocorre sob demanda."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        revision: str | None = None,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size deve ser maior que zero")
        self.model_id = model_id or os.getenv("NLP_EMBEDDING_MODEL", MODEL_ID_PADRAO)
        self.revision = revision or os.getenv("NLP_EMBEDDING_REVISION", "main")
        self.device = device or os.getenv("NLP_EMBEDDING_DEVICE", "cpu")
        self.batch_size = batch_size
        self._model: SentenceTransformer | None = None

    @property
    def model_version(self) -> str:
        return f"{self.model_id}@{self.revision}"

    def _carregar(self) -> SentenceTransformer:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Backend de embeddings ausente. Instale requirements-nlp.txt."
                ) from exc
            self._model = SentenceTransformer(
                self.model_id,
                revision=self.revision,
                device=self.device,
            )
            self._model.max_seq_length = MAX_TOKENS
        return self._model

    def load(self) -> None:
        """Carrega o modelo explicitamente para medir inicialização/warm-up."""
        self._carregar()

    def encode(self, texts: list[str], *, input_type: TipoTexto) -> EmbeddingBatch:
        """Codifica textos em lote com prefixo E5 e norma L2 unitária."""
        if input_type not in ("query", "passage"):
            raise ValueError("input_type deve ser 'query' ou 'passage'")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("textos devem ser strings não vazias")
        if not texts:
            return EmbeddingBatch(self.model_version, input_type, [])

        inputs = [f"{input_type}: {text.strip()}" for text in texts]
        embeddings = self._carregar().encode(
            inputs,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = embeddings.tolist()
        if any(len(vector) != DIMENSAO_EMBEDDING for vector in vectors):
            raise RuntimeError(
                f"Dimensão de embedding inesperada; esperado {DIMENSAO_EMBEDDING}"
            )
        return EmbeddingBatch(self.model_version, input_type, vectors)
