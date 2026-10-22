"""Persistência atômica das publicações validadas de pagamentos."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Iterable

from sqlalchemy import select, text

from app.api.schemas.pagamentos import TcePagamentosResponse
from app.core import database, orm
from app.core.models import (
    FornecedorCache,
    TceOrgaoStaging,
    TcePagamentoConciliado,
    TcePagamentoPagina,
    TcePagamentoRun,
    TcePagamentoStaging,
)
from app.core.porte_models import PorteObservacao, PortePublicacao
from app.pipeline.pagamentos import ResultadoPagamentos


def _json(valor: Any) -> Any:
    return json.loads(json.dumps(valor, ensure_ascii=False, default=str))


def _checksum(valor: Any) -> str:
    serializado = json.dumps(valor, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def montar_classificador_cadastral(
    cnpjs: Iterable[str], competencias: Iterable[str]
) -> tuple[Any, dict[str, Any]]:
    """Prefere o retrato mensal; usa o cache atual apenas como fallback parcial."""
    cnpjs_unicos = tuple(dict.fromkeys(str(item) for item in cnpjs if item))
    raizes = tuple(dict.fromkeys(item[:8] for item in cnpjs_unicos))
    competencias_unicas = tuple(dict.fromkeys(str(item) for item in competencias if item))
    historico: dict[tuple[str, str], dict[str, Any]] = {}
    atual: dict[str, dict[str, Any]] = {}
    database.init_db()
    with orm.main_session() as session:
        if raizes and competencias_unicas:
            linhas = session.execute(
                select(PortePublicacao, PorteObservacao)
                .join(PorteObservacao, PorteObservacao.versao == PortePublicacao.versao)
                .where(
                    PortePublicacao.ativa.is_(True),
                    PortePublicacao.competencia.in_(competencias_unicas),
                    PorteObservacao.raiz.in_(raizes),
                )
            ).all()
            for publicacao, observacao in linhas:
                historico[(observacao.raiz, publicacao.competencia)] = {
                    "porte_codigo": observacao.porte_codigo,
                    "optante_mei": observacao.optante_mei,
                    "fonte": publicacao.fonte,
                    "referencia": publicacao.competencia,
                    "procedencia_temporal": "RETRATO_MENSAL_PUBLICADO",
                    "temporal_comprovada": True,
                }
        if cnpjs_unicos:
            caches = session.scalars(
                select(FornecedorCache).where(FornecedorCache.cnpj.in_(cnpjs_unicos))
            ).all()
            for cache in caches:
                atual[cache.cnpj] = {
                    **dict(cache.dados_normalizados or {}),
                    "fonte": "RECEITA_FEDERAL_VIA_OPENCNPJ",
                    "referencia": cache.observado_em.date().isoformat(),
                    "procedencia_temporal": "RETRATO_ATUAL",
                    "temporal_comprovada": False,
                }

    def classificar(cnpj: str, competencia: str) -> dict[str, Any] | None:
        return historico.get((cnpj[:8], competencia)) or atual.get(cnpj)

    agora = datetime.now(timezone.utc)
    return classificar, {
        "fonte": "RECEITA_FEDERAL",
        "referencia": max(competencias_unicas, default=agora.date().isoformat()),
        "consultado_em": agora,
        "procedencia_temporal": "RETRATO_MENSAL_COM_FALLBACK_ATUAL",
    }


def publicar_pagamentos(
    *,
    resultado: ResultadoPagamentos,
    orgaos: Iterable[dict[str, Any]],
    pagamentos: Iterable[dict[str, Any]],
    paginas: Iterable[dict[str, Any]] = (),
) -> int:
    if not resultado.publicavel:
        raise ValueError("Publicação de pagamentos bloqueada pela validação de qualidade.")
    resposta = resultado.resposta.model_dump(mode="json")
    escopo = resultado.resposta.escopo
    agora = datetime.now(timezone.utc)
    database.init_db()
    with orm.main_session() as session:
        chave_lock = (
            f"tce-pagamentos:{escopo.codigo_municipio_tce}:"
            f"{escopo.exercicio_orcamento}:{escopo.competencia_corte}"
        )
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"),
            {"escopo": chave_lock},
        )
        anterior = session.scalar(
            select(TcePagamentoRun).where(
                TcePagamentoRun.codigo_municipio_tce == escopo.codigo_municipio_tce,
                TcePagamentoRun.exercicio_orcamento == escopo.exercicio_orcamento,
                TcePagamentoRun.competencia_corte == escopo.competencia_corte,
                TcePagamentoRun.status == "PUBLICADO",
            ).with_for_update()
        )
        run = TcePagamentoRun(
            codigo_municipio_tce=escopo.codigo_municipio_tce,
            codigo_municipio_ibge=escopo.codigo_municipio_ibge,
            municipio=escopo.municipio,
            exercicio_orcamento=escopo.exercicio_orcamento,
            competencia_corte=escopo.competencia_corte,
            status="EM_VALIDACAO",
            qualidade_status=resultado.resposta.qualidade.status.value,
            resposta=resposta,
            problemas=[],
            iniciado_em=agora,
        )
        session.add(run)
        session.flush()

        orgaos_unicos = {str(item["codigo_orgao"]): item for item in orgaos}
        session.add_all(
            TceOrgaoStaging(
                run_id=run.id,
                codigo_orgao=item["codigo_orgao"],
                nome_orgao=item["nome_orgao"],
                administracao=item["administracao"],
                payload=_json(item.get("payload_bruto") or item),
            )
            for item in orgaos_unicos.values()
        )
        pagamentos_unicos = {str(item["chave_pagamento"]): item for item in pagamentos}
        session.add_all(
            TcePagamentoStaging(
                run_id=run.id,
                chave_pagamento=item["chave_pagamento"],
                chave_empenho=item["chave_empenho"],
                competencia_documento=item["competencia_documento"],
                data_pagamento=date.fromisoformat(str(item["data_pagamento"])),
                valor_pagamento_centavos=int(item["valor_pagamento_centavos"]),
                estado_de_estornado=int(item["estado_de_estornado"]),
                payload=_json(item.get("payload_bruto") or item),
            )
            for item in pagamentos_unicos.values()
        )
        session.add_all(
            TcePagamentoConciliado(
                run_id=run.id,
                chave_pagamento=item["chave_pagamento"],
                chave_empenho=item["chave_empenho"],
                codigo_orgao=item["codigo_orgao"],
                codigo_elemento_despesa=item["codigo_elemento_despesa"],
                data_pagamento=date.fromisoformat(str(item["data_pagamento"])),
                valor_pagamento_centavos=int(item["valor_pagamento_centavos"]),
                documento_fornecedor=item.get("documento_fornecedor"),
                cnpj_fornecedor=item.get("cnpj_fornecedor"),
                raiz_cnpj_fornecedor=item.get("raiz_cnpj_fornecedor"),
                cpf_fornecedor=item.get("cpf_fornecedor"),
                categoria_fornecedor=item["categoria_fornecedor"],
                classificacao_cadastral=_json(item.get("classificacao_cadastral")),
            )
            for item in resultado.pagamentos_conciliados
        )
        session.add_all(
            TcePagamentoPagina(
                run_id=run.id,
                endpoint=item["endpoint"],
                competencia=item.get("competencia") or "",
                start_index=int(item.get("start_index", 0)),
                quantidade=int(item["quantidade"]),
                checksum=item.get("checksum") or _checksum(item.get("registros", [])),
            )
            for item in paginas
        )
        session.flush()
        if anterior is not None:
            anterior.status = "SUBSTITUIDO"
            anterior.substituido_em = agora
        run.status = "PUBLICADO"
        run.publicado_em = agora
        session.flush()
        return run.id


def obter_publicacao(
    *, codigo_municipio_tce: str, exercicio_orcamento: int, competencia_corte: str
) -> TcePagamentosResponse | None:
    database.init_db()
    with orm.main_session() as session:
        resposta = session.scalar(
            select(TcePagamentoRun.resposta).where(
                TcePagamentoRun.codigo_municipio_tce == codigo_municipio_tce,
                TcePagamentoRun.exercicio_orcamento == exercicio_orcamento,
                TcePagamentoRun.competencia_corte == competencia_corte,
                TcePagamentoRun.status == "PUBLICADO",
            )
        )
    return TcePagamentosResponse.model_validate(resposta) if resposta else None


__all__ = [
    "montar_classificador_cadastral",
    "obter_publicacao",
    "publicar_pagamentos",
]
