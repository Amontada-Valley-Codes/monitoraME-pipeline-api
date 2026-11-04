"""Cadastro e filtro auditável dos órgãos do TCE-CE."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import TceOrgaoClassificacao
from app.utils import normalizar_texto


def classificar_orgao(orgao: dict[str, Any]) -> dict[str, str | None]:
    codigo = str(orgao.get("codigo_orgao", "")).strip()
    nome_original = str(orgao.get("nome_orgao", "")).strip()
    tipo = str(orgao.get("codigo_tipo_unidade", "")).strip() or None
    if not codigo or not nome_original:
        raise ValueError("Cadastro de órgão sem código ou nome.")
    nome = normalizar_texto(nome_original)
    tokens = re.sub(r"[^A-Z0-9]+", " ", nome).split()
    camara = "CAMARA" in tokens or "LEGISLATIVO" in tokens
    certeza = any(token in tokens for token in (
        "CAMARA", "LEGISLATIVO", "PREFEITURA", "SECRETARIA", "GABINETE",
        "AUTARQUIA", "FUNDO", "PROCURADORIA", "INSTITUTO", "SEC",
    )) or any(token.startswith("CONTROL") for token in tokens)
    return {
        "codigo_orgao": codigo,
        "nome_orgao": nome_original,
        "codigo_tipo_unidade": tipo,
        "classificacao": "CAMARA" if camara else "EXECUTIVO_MUNICIPAL",
        "regra": "NOME_ORGAO_NORMALIZADO",
        "confianca": "ALTA" if certeza else "REVISAR",
    }


def salvar_classificacoes(
    session: Session, *, codigo_municipio_tce: str, exercicio_orcamento: int,
    orgaos: list[dict[str, Any]],
) -> list[dict[str, str | None]]:
    classificados = [classificar_orgao(orgao) for orgao in orgaos]
    codigos = [item["codigo_orgao"] for item in classificados]
    if len(codigos) != len(set(codigos)):
        raise ValueError("Cadastro de órgãos contém códigos duplicados.")
    agora = datetime.now(timezone.utc)
    for item in classificados:
        session.execute(
            insert(TceOrgaoClassificacao).values(
                codigo_municipio_tce=codigo_municipio_tce,
                exercicio_orcamento=exercicio_orcamento,
                **item,
                fonte="TCE-CE /orgaos",
                observado_em=agora,
            ).on_conflict_do_update(
                constraint="uq_tce_orgao_classificacao_escopo",
                set_={**item, "fonte": "TCE-CE /orgaos", "observado_em": agora},
            )
        )
    return classificados


def executivo_tce(codigo_municipio: Any, exercicio: Any, codigo_orgao: Any):
    """Expressão SQL: falha fechada para órgão ausente ou ainda não classificado."""
    return exists(select(1).where(
        TceOrgaoClassificacao.codigo_municipio_tce == codigo_municipio,
        TceOrgaoClassificacao.exercicio_orcamento == exercicio,
        TceOrgaoClassificacao.codigo_orgao == codigo_orgao,
        TceOrgaoClassificacao.classificacao == "EXECUTIVO_MUNICIPAL",
        TceOrgaoClassificacao.confianca == "ALTA",
    ))
