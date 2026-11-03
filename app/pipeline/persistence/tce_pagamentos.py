"""Persistência versionada de notas de pagamento do TCE-CE por competência."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Iterable

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.models import TcePagamento, TcePagamentoIngestionRun


STATUS_EM_VALIDACAO = "EM_VALIDACAO"
STATUS_PUBLICADO = "PUBLICADO"
STATUS_SUBSTITUIDO = "SUBSTITUIDO"
STATUS_REJEITADO = "REJEITADO"
RETENCAO_LOTES_SUBSTITUIDOS = timedelta(days=7)


@dataclass(frozen=True)
class ResultadoPublicacaoPagamentos:
    run_id: int
    status: str
    codigo_municipio_tce: str
    exercicio_orcamento: int
    competencia: str
    quantidade_pagamentos: int
    problemas: list[dict[str, Any]]
    run_anterior_id: int | None = None


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _payload(valor: Any) -> Any:
    return json.loads(json.dumps(valor, ensure_ascii=False, default=str))


def _trancar_escopo(
    session: Session, codigo_municipio_tce: str, exercicio_orcamento: int, competencia: str,
) -> None:
    escopo = f"tce-pagamentos:{codigo_municipio_tce}:{exercicio_orcamento}:{competencia}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"), {"escopo": escopo})


def _run_publicado(
    session: Session, codigo_municipio_tce: str, exercicio_orcamento: int, competencia: str,
) -> TcePagamentoIngestionRun | None:
    return session.scalar(
        select(TcePagamentoIngestionRun).where(
            TcePagamentoIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TcePagamentoIngestionRun.exercicio_orcamento == exercicio_orcamento,
            TcePagamentoIngestionRun.competencia == competencia,
            TcePagamentoIngestionRun.status == STATUS_PUBLICADO,
        ).with_for_update()
    )


def _deduplicar(registros: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    por_chave: dict[str, dict[str, Any]] = {}
    problemas = []
    for registro in registros:
        chave = str(registro.get("chave_pagamento") or "").strip()
        if not chave:
            problemas.append({"codigo": "CHAVE_AUSENTE", "mensagem": "Nota sem chave_pagamento."})
            continue
        anterior = por_chave.get(chave)
        if anterior is None:
            por_chave[chave] = registro
        elif anterior != registro:
            problemas.append({
                "codigo": "DUPLICIDADE_CONFLITANTE",
                "mensagem": "Nota de pagamento repetida com conteúdo diferente.",
                "chave": chave,
            })
    return list(por_chave.values()), problemas


def _modelo_pagamento(run_id: int, registro: dict[str, Any]) -> TcePagamento:
    return TcePagamento(
        run_id=run_id,
        chave_pagamento=registro["chave_pagamento"],
        chave_empenho=registro["chave_empenho"],
        codigo_orgao=registro["codigo_orgao"],
        codigo_unidade_orcamentaria=registro["codigo_unidade_orcamentaria"],
        data_emissao_empenho=date.fromisoformat(registro["data_emissao_empenho"]),
        numero_empenho=registro["numero_empenho"],
        numero_sub_empenho=registro["numero_sub_empenho"],
        numero_nota_pagamento=registro["numero_nota_pagamento"],
        data_nota_pagamento=date.fromisoformat(registro["data_nota_pagamento"]),
        data_referencia_doc=registro["data_referencia_doc"],
        estado_de_estornado=registro["estado_de_estornado"],
        valor_nota_pagamento_centavos=registro["valor_nota_pagamento_centavos"],
        payload=_payload(registro["payload_bruto"]),
    )


def publicar_lote(
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
    competencia: str,
    pagamentos: Iterable[dict[str, Any]],
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoPagamentos:
    """Publica o snapshot mensal completo; falhas preservam o lote vigente."""
    database.init_db()
    inicio, agora = iniciado_em or _agora(), _agora()
    registros, problemas = _deduplicar(pagamentos)
    referencia_esperada = int(competencia.replace("-", ""))
    for registro in registros:
        if registro.get("codigo_municipio_tce") != codigo_municipio_tce:
            problemas.append({"codigo": "MUNICIPIO_DIVERGENTE", "chave": registro["chave_pagamento"]})
        if registro.get("exercicio_orcamento") != exercicio_orcamento * 100:
            problemas.append({"codigo": "EXERCICIO_DIVERGENTE", "chave": registro["chave_pagamento"]})
        if registro.get("competencia") != competencia or registro.get("data_referencia_doc") != referencia_esperada:
            problemas.append({"codigo": "COMPETENCIA_DIVERGENTE", "chave": registro["chave_pagamento"]})

    with orm.main_session() as session:
        _trancar_escopo(session, codigo_municipio_tce, exercicio_orcamento * 100, competencia)
        session.execute(delete(TcePagamentoIngestionRun).where(
            TcePagamentoIngestionRun.status.in_((STATUS_SUBSTITUIDO, STATUS_REJEITADO)),
            TcePagamentoIngestionRun.expira_em.is_not(None),
            TcePagamentoIngestionRun.expira_em <= agora,
        ))
        anterior = _run_publicado(session, codigo_municipio_tce, exercicio_orcamento * 100, competencia)
        run = TcePagamentoIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio_orcamento * 100,
            competencia=competencia,
            status=STATUS_EM_VALIDACAO,
            iniciado_em=inicio,
            quantidade_pagamentos=len(registros),
            problemas=_payload(problemas),
        )
        session.add(run)
        session.flush()
        if problemas:
            run.status = STATUS_REJEITADO
            run.finalizado_em = agora
            run.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            run.erro = "; ".join(p["codigo"] for p in problemas)
            return ResultadoPublicacaoPagamentos(
                run.id, run.status, codigo_municipio_tce, exercicio_orcamento * 100,
                competencia, len(registros), problemas, anterior.id if anterior else None,
            )

        session.add_all(_modelo_pagamento(run.id, registro) for registro in registros)
        session.flush()
        if anterior is not None:
            anterior.status = STATUS_SUBSTITUIDO
            anterior.substituido_em = agora
            anterior.expira_em = agora + RETENCAO_LOTES_SUBSTITUIDOS
            session.flush()
        run.status = STATUS_PUBLICADO
        run.finalizado_em = agora
        run.publicado_em = agora
        session.flush()
        return ResultadoPublicacaoPagamentos(
            run.id, run.status, codigo_municipio_tce, exercicio_orcamento * 100,
            competencia, len(registros), [], anterior.id if anterior else None,
        )


def registrar_lote_rejeitado(
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
    competencia: str,
    erro: str,
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoPagamentos:
    """Registra erro de fonte/normalização sem substituir o último snapshot."""
    database.init_db()
    agora = _agora()
    with orm.main_session() as session:
        _trancar_escopo(session, codigo_municipio_tce, exercicio_orcamento * 100, competencia)
        anterior = _run_publicado(session, codigo_municipio_tce, exercicio_orcamento * 100, competencia)
        run = TcePagamentoIngestionRun(
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio_orcamento * 100,
            competencia=competencia,
            status=STATUS_REJEITADO,
            iniciado_em=iniciado_em or agora,
            finalizado_em=agora,
            expira_em=agora + RETENCAO_LOTES_SUBSTITUIDOS,
            quantidade_pagamentos=0,
            problemas=[{"codigo": "FALHA_INGESTAO", "mensagem": erro}],
            erro=erro,
        )
        session.add(run)
        session.flush()
        return ResultadoPublicacaoPagamentos(
            run.id, run.status, codigo_municipio_tce, exercicio_orcamento * 100,
            competencia, 0, [{"codigo": "FALHA_INGESTAO", "mensagem": erro}],
            anterior.id if anterior else None,
        )
