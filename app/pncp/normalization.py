"""Normalizacao de RAW PNCP sem acesso a rede."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import orm
from app.core.models import PncpOportunidade, PncpQuarentena, RawPncpRecord
from app.nlp.corpus import sincronizar_corpus
from app.pncp.jobs import JobReivindicado, NovoJob, agendar_job_session
from app.pncp.opportunities import registrar_eventos_alteracao_session
from app.pncp.quality import RULE_VERSION, avaliar_payload, registrar_avaliacao_session
from app.pncp.raw import JOB_NORMALIZAR, PRIORIDADE_NORMALIZACAO
from app.pncp.urls import url_edital_oficial
from app.pncp.derived_status import aplicar_status_session


def agendar_reprocessamento_regra_session(session: Session, *, limite: int = 2000) -> dict[str, int | str]:
    """Agenda RAWs atuais cuja oportunidade ainda usa regra de normalizacao antiga."""
    if limite < 1:
        raise ValueError("limite deve ser maior que zero")
    regra_atual = func.coalesce(PncpOportunidade.dados_semanticos["rule_version"].as_string(), "")
    raws = session.scalars(
        select(RawPncpRecord)
        .join(PncpOportunidade,
              (PncpOportunidade.source == RawPncpRecord.source)
              & (PncpOportunidade.external_id == RawPncpRecord.external_id)
              & (PncpOportunidade.payload_hash == RawPncpRecord.payload_hash))
        .where(RawPncpRecord.entity_type == "CONTRATACAO", regra_atual != RULE_VERSION)
        .order_by(RawPncpRecord.id)
        .limit(limite)
    ).all()
    agendados = 0
    for raw in raws:
        _, criado = agendar_job_session(session, NovoJob(
            job_type=JOB_NORMALIZAR,
            entity_type=raw.entity_type,
            source=raw.source,
            scope_key=str(raw.id),
            dedupe_key=f"normalize:PNCP:{raw.id}:rule:{RULE_VERSION}",
            priority=PRIORIDADE_NORMALIZACAO,
            next_run_at=datetime.now(timezone.utc),
            raw_record_id=raw.id,
        ))
        agendados += int(criado)
    return {"oportunidades_encontradas": len(raws), "jobs_criados": agendados, "rule_version": RULE_VERSION}

class ErroNormalizacaoPncp(ValueError):
    """Inconsistência estrutural: preservar RAW e aguardar replay autorizado."""

    retryable = False
    quarantinar = True

    def contexto(self) -> dict[str, object]:
        return {"categoria": "normalizacao_estrutural", "retryable": False}


def _data(valor: Any) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalizar_payload(raw: RawPncpRecord) -> dict[str, Any]:
    avaliacao = avaliar_payload(raw)
    if avaliacao["data_quality_status"] == "invalid":
        motivos = [f"{campo}:{e['motivo']}" for campo, e in avaliacao["campos"].items() if e["status"] == "error"]
        raise ErroNormalizacaoPncp("; ".join(motivos))
    p = raw.payload
    campos = avaliacao["campos"]
    orgao = dict(p["orgaoEntidade"]) if isinstance(p.get("orgaoEntidade"), dict) else None
    unidade = dict(p["unidadeOrgao"]) if isinstance(p.get("unidadeOrgao"), dict) else None
    if orgao is not None:
        orgao["cnpj"] = campos["orgaoEntidade.cnpj"]["valor"]
    if unidade is not None:
        unidade["codigoIbge"] = campos["unidadeOrgao.codigoIbge"]["valor"]
    semanticos = {
        e["destino"].split(".", 1)[1]: e["valor"]
        for e in campos.values()
        if e["destino"].startswith("dados_semanticos.")
    }
    semanticos["prazo_proposta_final_origem"] = campos["dataEncerramentoProposta"]["valor"]
    semanticos["forma_participacao_origem"] = p.get("modoDisputaNome") or p.get("modoDisputa")
    semanticos["rule_version"] = avaliacao["rule_version"]
    return {
        "source": raw.source,
        "external_id": campos["numeroControlePNCP"]["valor"],
        "raw_record_id": raw.id,
        "payload_hash": raw.payload_hash,
        "source_updated_at": raw.source_updated_at,
        "orgao": orgao,
        "unidade": unidade,
        "objeto": campos["objetoCompra"]["valor"],
        "modalidade": p.get("modalidadeNome") or p.get("modalidadeContratacao"),
        "situacao": p.get("situacaoCompraNome") or p.get("situacaoCompra"),
        "prazo_proposta_final": (
            datetime.fromisoformat(campos["dataEncerramentoProposta"]["valor"]["valor"])
            if campos["dataEncerramentoProposta"]["valor"]
            and campos["dataEncerramentoProposta"]["valor"]["tipo"] == "instante"
            else None
        ),
        "url_oficial": (
            url_edital_oficial(
                raw.external_id,
                cnpj=(orgao or {}).get("cnpj"),
                ano=p.get("anoCompra"),
                sequencial=p.get("sequencialCompra"),
            )
            or p.get("linkSistemaOrigem")
            or f"https://pncp.gov.br/app/editais/{raw.external_id}"
        ),
        "data_quality_status": avaliacao["data_quality_status"],
        "dados_semanticos": semanticos,
    }


def _chave_versao(dados: dict[str, Any]) -> tuple[str, date | datetime] | None:
    global_origem = (dados.get("dados_semanticos") or {}).get("data_atualizacao_global")
    if global_origem:
        valor = (
            date.fromisoformat(global_origem["valor"])
            if global_origem["tipo"] == "civil"
            else datetime.fromisoformat(global_origem["valor"])
        )
        return "global:" + global_origem["tipo"], valor
    atualizado = dados.get("source_updated_at")
    if atualizado is not None and atualizado.tzinfo is not None:
        return "instante", atualizado.astimezone(timezone.utc)
    origem = (dados.get("dados_semanticos") or {}).get("data_atualizacao")
    if not origem:
        return None
    if origem["tipo"] == "civil":
        return "civil", date.fromisoformat(origem["valor"])
    return origem["tipo"], datetime.fromisoformat(origem["valor"])


def normalizar_raw_session(session: Session, raw: RawPncpRecord) -> str:
    avaliacao = avaliar_payload(raw)
    evidencia = registrar_avaliacao_session(session, raw, avaliacao)

    def concluir(resultado: str) -> str:
        evidencia.decisao = resultado
        return resultado

    dados = normalizar_payload(raw)
    # Serializa versões concorrentes da mesma contratação antes de comparar o
    # hash e a precedência temporal, evitando que uma resposta antiga vença por
    # ordem de commit quando dois workers normalizam RAWs diferentes.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"{raw.source}:{dados['external_id']}"},
    )
    atual = session.get(PncpOportunidade, {"source": raw.source, "external_id": raw.external_id})
    if (
        atual
        and atual.payload_hash == raw.payload_hash
        and (atual.dados_semanticos or {}).get("rule_version") == avaliacao["rule_version"]
    ):
        return concluir("INALTERADO")
    if atual and atual.payload_hash != raw.payload_hash:
        anterior_versao = _chave_versao({"source_updated_at": atual.source_updated_at, "dados_semanticos": atual.dados_semanticos})
        nova_versao = _chave_versao(dados)
        if anterior_versao is None or nova_versao is None or anterior_versao[0] != nova_versao[0]:
            return concluir("AMBIGUO")
        if nova_versao[1] < anterior_versao[1]:
            return concluir("ANTIGO")
        if nova_versao[1] == anterior_versao[1]:
            return concluir("AMBIGUO")
    if atual and atual.payload_hash != raw.payload_hash:
        # Um snapshot menos completo nao deve apagar campos ja conhecidos.
        if any(
            getattr(atual, campo) is not None and dados[campo] is None
            for campo in ("orgao", "unidade", "objeto", "prazo_proposta_final")
        ):
            return concluir("INCOMPLETO")
        for campo, filho in (("orgao", "cnpj"), ("unidade", "codigoIbge")):
            anterior_entidade = getattr(atual, campo) or {}
            nova_entidade = dados[campo] or {}
            if anterior_entidade.get(filho) is not None and nova_entidade.get(filho) is None:
                return concluir("INCOMPLETO")
    if atual is None:
        novo = PncpOportunidade(**dados)
        session.add(novo)
        payload = raw.payload
        aplicar_status_session(
            session,
            novo,
            agora=datetime.now(timezone.utc),
            official_status=payload.get("situacaoCompraNome") or payload.get("situacaoCompra"),
            inicio=(dados.get("dados_semanticos") or {}).get("data_abertura_proposta"),
            encerramento=(dados.get("dados_semanticos") or {}).get("prazo_proposta_final_origem"),
            forma_participacao=payload.get("modoDisputaNome") or payload.get("modoDisputa"),
        )
        registrar_eventos_alteracao_session(session, anterior=None, atual=novo)
        return concluir("NOVO")
    anterior = PncpOportunidade(**{campo: getattr(atual, campo) for campo in dados})
    for campo, valor in dados.items():
        setattr(atual, campo, valor)
    atual.normalized_at = datetime.now(timezone.utc)
    payload = raw.payload
    aplicar_status_session(
        session,
        atual,
        agora=atual.normalized_at,
        official_status=payload.get("situacaoCompraNome") or payload.get("situacaoCompra"),
        inicio=(dados.get("dados_semanticos") or {}).get("data_abertura_proposta"),
        encerramento=(dados.get("dados_semanticos") or {}).get("prazo_proposta_final_origem"),
        forma_participacao=payload.get("modoDisputaNome") or payload.get("modoDisputa"),
    )
    registrar_eventos_alteracao_session(session, anterior=anterior, atual=atual)
    return concluir("ALTERADO")


def normalizar_job(job: JobReivindicado) -> str:
    if job.raw_record_id is None:
        raise ValueError("job normalize sem raw_record_id")
    erro = None
    resultado = None
    with orm.main_session() as session:
        raw = session.get(RawPncpRecord, job.raw_record_id)
        if raw is None:
            raise ValueError("RAW inexistente")
        try:
            resultado = normalizar_raw_session(session, raw)
            if resultado in {"NOVO", "ALTERADO", "INALTERADO", "INCOMPLETO"}:
                session.flush()
                sincronizar_corpus(
                    session,
                    opportunity_keys=[(raw.source, raw.external_id)],
                )
            # Um RAW valido pode perder por precedencia sem ser estruturalmente
            # invalido; uma avaliacao concluida resolve a quarentena estrutural.
            quarentena = session.get(PncpQuarentena, raw.id)
            if quarentena is not None:
                session.delete(quarentena)
        except ErroNormalizacaoPncp as exc:
            # Confirmar evidencias e quarentena antes de sinalizar falha ao worker.
            if session.get(PncpQuarentena, raw.id) is None:
                session.add(PncpQuarentena(raw_record_id=raw.id, motivo=str(exc)))
            erro = exc
    if erro is not None:
        raise erro
    return resultado
