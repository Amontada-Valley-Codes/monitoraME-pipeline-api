"""Revisões imutáveis do de-para CNAE/categoria para oportunidades PNCP."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.models import (
    PncpMapaCnaeCategoria, PncpMapaRevisao,
    PncpTaxonomiaCategoria, PncpTaxonomiaCnae,
)


def criar_revisao(session: Session, *, versao: str, evidencia: str, responsavel: str) -> PncpMapaRevisao:
    if session.get(PncpMapaRevisao, versao):
        raise ValueError("Versão de mapa já existe; revisões publicadas são imutáveis")
    if not versao.strip() or not evidencia.strip() or not responsavel.strip():
        raise ValueError("Versão, evidência e responsável são obrigatórios")
    revisao = PncpMapaRevisao(versao=versao, estado="RASCUNHO", evidencia=evidencia, criado_por=responsavel)
    session.add(revisao)
    return revisao


def copiar_relacoes(session: Session, *, versao_origem: str, versao_destino: str) -> int:
    """Inicializa um rascunho como snapshot completo da revisão de origem."""
    origem = session.get(PncpMapaRevisao, versao_origem)
    destino = session.get(PncpMapaRevisao, versao_destino)
    if origem is None or destino is None:
        raise ValueError("Revisão de origem ou destino inexistente")
    if destino.estado != "RASCUNHO":
        raise ValueError("A revisão destino precisa estar em RASCUNHO")
    if session.query(PncpMapaCnaeCategoria).filter_by(versao=versao_destino).first():
        raise ValueError("A revisão destino já possui relações")
    origem_relacoes = session.query(PncpMapaCnaeCategoria).filter_by(versao=versao_origem).all()
    for relacao in origem_relacoes:
        session.add(PncpMapaCnaeCategoria(
            versao=versao_destino, cnae_codigo=relacao.cnae_codigo,
            categoria_slug=relacao.categoria_slug, tipo=relacao.tipo,
            confianca=relacao.confianca, ativo=relacao.ativo,
            condicao=relacao.condicao, evidencias=list(relacao.evidencias or []),
            revisado_por=relacao.revisado_por,
        ))
    return len(origem_relacoes)


def adicionar_relacao(
    session: Session, *, versao: str, cnae: str, categoria: str, tipo: str,
    confianca: float, responsavel: str, evidencias: list[str], ativo: bool = True,
    condicao: str | None = None,
) -> PncpMapaCnaeCategoria:
    revisao = session.get(PncpMapaRevisao, versao)
    if revisao is None:
        raise ValueError("Revisão inexistente")
    if revisao.estado != "RASCUNHO":
        raise ValueError("Relações só podem ser alteradas em uma revisão RASCUNHO")
    if session.get(PncpTaxonomiaCnae, cnae) is None:
        raise ValueError(f"CNAE inexistente na taxonomia: {cnae}")
    if session.get(PncpTaxonomiaCategoria, categoria) is None:
        raise ValueError(f"Categoria inexistente na taxonomia: {categoria}")
    if len(cnae) != 7 or not cnae.isdigit():
        raise ValueError("CNAE deve conter sete dígitos")
    if tipo not in {"DIRETA", "RELACIONADA", "CONDICIONAL"} or not 0 <= confianca <= 1:
        raise ValueError("Tipo ou confiança inválidos")
    if tipo == "CONDICIONAL" and not (condicao and condicao.strip()):
        raise ValueError("Relação condicional exige condição explícita")
    if not responsavel.strip() or not evidencias:
        raise ValueError("Responsável e evidência documentada são obrigatórios")
    relacao = PncpMapaCnaeCategoria(
        versao=versao, cnae_codigo=cnae, categoria_slug=categoria, tipo=tipo,
        confianca=confianca, ativo=ativo, condicao=condicao,
        evidencias=evidencias, revisado_por=responsavel,
    )
    session.add(relacao)
    return relacao


def atualizar_relacao(
    session: Session, *, versao: str, cnae: str, categoria: str, tipo: str,
    confianca: float, responsavel: str, evidencias: list[str], ativo: bool,
    condicao: str | None = None,
) -> PncpMapaCnaeCategoria:
    revisao = session.get(PncpMapaRevisao, versao)
    if revisao is None or revisao.estado != "RASCUNHO":
        raise ValueError("Relações só podem ser alteradas em uma revisão RASCUNHO")
    relacao = session.query(PncpMapaCnaeCategoria).filter_by(
        versao=versao, cnae_codigo=cnae, categoria_slug=categoria,
    ).one_or_none()
    if relacao is None:
        raise ValueError("Relação inexistente na revisão")
    if session.get(PncpTaxonomiaCnae, cnae) is None or len(cnae) != 7 or not cnae.isdigit():
        raise ValueError(f"CNAE inexistente ou inválido: {cnae}")
    if session.get(PncpTaxonomiaCategoria, categoria) is None:
        raise ValueError(f"Categoria inexistente na taxonomia: {categoria}")
    if tipo not in {"DIRETA", "RELACIONADA", "CONDICIONAL"} or not 0 <= confianca <= 1:
        raise ValueError("Tipo ou confiança inválidos")
    if tipo == "CONDICIONAL" and not (condicao and condicao.strip()):
        raise ValueError("Relação condicional exige condição explícita")
    if not responsavel.strip() or not evidencias:
        raise ValueError("Responsável e evidência documentada são obrigatórios")
    relacao.tipo, relacao.confianca = tipo, confianca
    relacao.ativo, relacao.condicao = ativo, condicao
    relacao.evidencias, relacao.revisado_por = evidencias, responsavel
    relacao.revisado_em = datetime.now(timezone.utc)
    return relacao


def desativar_relacao(
    session: Session, *, versao: str, cnae: str, categoria: str, responsavel: str,
    evidencia: str,
) -> PncpMapaCnaeCategoria:
    revisao = session.get(PncpMapaRevisao, versao)
    if revisao is None or revisao.estado != "RASCUNHO":
        raise ValueError("Relações só podem ser desativadas em uma revisão RASCUNHO")
    if not responsavel.strip() or not evidencia.strip():
        raise ValueError("Responsável e evidência da desativação são obrigatórios")
    relacao = session.query(PncpMapaCnaeCategoria).filter_by(
        versao=versao, cnae_codigo=cnae, categoria_slug=categoria,
    ).one_or_none()
    if relacao is None:
        raise ValueError("Relação inexistente na revisão")
    relacao.ativo = False
    relacao.revisado_por = responsavel
    relacao.revisado_em = datetime.now(timezone.utc)
    relacao.evidencias = list(relacao.evidencias or []) + [evidencia]
    return relacao


def ativar_revisao(session: Session, *, versao: str, responsavel: str) -> None:
    if not responsavel.strip():
        raise ValueError("Responsável pela ativação é obrigatório")
    revisao = session.get(PncpMapaRevisao, versao)
    if revisao is None or revisao.estado != "RASCUNHO":
        raise ValueError("Somente uma revisão RASCUNHO existente pode ser ativada")
    if not session.query(PncpMapaCnaeCategoria).filter_by(versao=versao, ativo=True).first():
        raise ValueError("Não é possível ativar uma revisão sem relações ativas")
    anterior = session.query(PncpMapaRevisao).filter_by(estado="ATIVA").with_for_update().one_or_none()
    if anterior:
        anterior.estado = "SUBSTITUIDA"
    revisao.estado = "ATIVA"
    revisao.ativado_por = responsavel
    revisao.ativado_em = datetime.now(timezone.utc)


def obter_versao_ativa(session: Session) -> str | None:
    revisao = session.query(PncpMapaRevisao).filter_by(estado="ATIVA").one_or_none()
    return revisao.versao if revisao else None


def obter_snapshot(session: Session, versao: str | None = None) -> dict:
    versao = versao or obter_versao_ativa(session)
    if versao is None:
        raise LookupError("Nenhuma versão ativa do mapa CNAE-categoria")
    revisao = session.get(PncpMapaRevisao, versao)
    if revisao is None:
        raise LookupError(f"Versão de mapa inexistente: {versao}")
    relacoes = (
        session.query(PncpMapaCnaeCategoria, PncpTaxonomiaCnae, PncpTaxonomiaCategoria)
        .join(PncpTaxonomiaCnae, PncpTaxonomiaCnae.codigo == PncpMapaCnaeCategoria.cnae_codigo)
        .join(PncpTaxonomiaCategoria, PncpTaxonomiaCategoria.slug == PncpMapaCnaeCategoria.categoria_slug)
        .filter(PncpMapaCnaeCategoria.versao == versao)
        .all()
    )
    todas_cnaes = session.query(PncpTaxonomiaCnae.codigo).all()
    todas_categorias = session.query(PncpTaxonomiaCategoria.slug).all()
    ativas = [r for r in relacoes if r[0].ativo]
    cnaes_mapeados = {r[0].cnae_codigo for r in ativas}
    categorias_mapeadas = {r[0].categoria_slug for r in ativas}
    cnae_categoria_count: dict[str, int] = {}
    categoria_cnae_count: dict[str, int] = {}
    for relacao, _, _ in ativas:
        cnae_categoria_count[relacao.cnae_codigo] = cnae_categoria_count.get(relacao.cnae_codigo, 0) + 1
        categoria_cnae_count[relacao.categoria_slug] = categoria_cnae_count.get(relacao.categoria_slug, 0) + 1
    total_cnaes, total_categorias = len(todas_cnaes), len(todas_categorias)
    return {
        "versao": versao, "estado": revisao.estado,
        "evidencia": revisao.evidencia, "criado_em": revisao.criado_em,
        "ativado_em": revisao.ativado_em,
        "relacoes": [{
            "cnae": r.cnae_codigo, "descricao_cnae": c.descricao,
            "categoria": cat.slug, "nome_categoria": cat.nome,
            "tipo": r.tipo, "confianca": r.confianca, "ativo": r.ativo,
            "condicao": r.condicao, "evidencias": r.evidencias,
            "revisado_por": r.revisado_por, "revisado_em": r.revisado_em,
        } for r, c, cat in relacoes],
        "metricas": {
            "cnaes_mapeados": len(cnaes_mapeados), "cnaes_taxonomia": total_cnaes,
            "cobertura_cnae": len(cnaes_mapeados) / total_cnaes if total_cnaes else 0,
            "categorias_mapeadas": len(categorias_mapeadas), "categorias_taxonomia": total_categorias,
            "categorias_sem_mapeamento": sorted({c[0] for c in todas_categorias} - categorias_mapeadas),
            "cnaes_sem_mapeamento": sorted({c[0] for c in todas_cnaes} - cnaes_mapeados),
            "cnaes_com_multiplas_categorias": sum(v > 1 for v in cnae_categoria_count.values()),
            "categorias_com_multiplos_cnaes": sum(v > 1 for v in categoria_cnae_count.values()),
            "relacoes_ativas": len(ativas), "relacoes_inativas": len(relacoes) - len(ativas),
        },
    }


def mapa_ativo_para_matching(session: Session) -> tuple[str, dict[str, set[str]]]:
    versao = obter_versao_ativa(session)
    if versao is None:
        raise LookupError("Nenhuma versão ativa do mapa CNAE-categoria")
    linhas = session.query(PncpMapaCnaeCategoria).filter_by(versao=versao, ativo=True).all()
    mapa: dict[str, set[str]] = {}
    for relacao in linhas:
        mapa.setdefault(relacao.cnae_codigo, set()).add(relacao.categoria_slug)
    return versao, mapa
