"""Prepara uma consulta semântica independente para cada CNAE do perfil."""
from __future__ import annotations

from dataclasses import dataclass

from app.nlp.embeddings.generator import gerar_embeddings_perfis
from app.nlp.embeddings.model import E5EmbeddingModel
from app.nlp.matching.postgres_retrieval import ConsultaCnae
from app.nlp.profiles.schemas import CompanyProfile


@dataclass(frozen=True)
class PerfilSemantico:
    consultas: tuple[ConsultaCnae, ...]
    cnaes_sem_descricao: tuple[str, ...]
    model_version: str


def construir_perfil_semantico(
    profile: CompanyProfile,
    model: E5EmbeddingModel,
    *,
    descricoes_catalogo: dict[str, str] | None = None,
) -> PerfilSemantico:
    """Gera um vetor por CNAE; não concatena atividades distintas.

    Descrições ausentes só são preenchidas quando fornecidas por um catálogo
    externo versionado pelo chamador. CNAEs sem texto são reportados e omitidos.
    """
    catalog = descricoes_catalogo or {}
    selecionados = [
        (cnae.code, (cnae.description or catalog.get(cnae.code) or "").strip())
        for cnae in profile.cnaes
    ]
    contextos = [profile.business_description] if profile.business_description else []
    for servico in profile.products_and_services[:10]:
        contexto_servico = " ".join([servico.description, *servico.keywords[:10]])
        if contexto_servico:
            contextos.append(contexto_servico)
    contexto_empresa = " | ".join(contextos)[:1_200]
    com_texto: list[tuple[str, str]] = []
    for code, description in selecionados:
        if code == profile.primary_cnae and contexto_empresa:
            contexto = f"Contexto da empresa: {contexto_empresa}"
            description = f"{description}. {contexto}" if description else contexto
        if description:
            com_texto.append((code, description))
    ausentes = tuple(code for code, description in selecionados if not description)
    batch = gerar_embeddings_perfis(model, [description for _, description in com_texto])
    consultas = tuple(
        ConsultaCnae(code, vector)
        for (code, _), vector in zip(com_texto, batch.vectors, strict=True)
    )
    return PerfilSemantico(consultas, ausentes, batch.model_version)
