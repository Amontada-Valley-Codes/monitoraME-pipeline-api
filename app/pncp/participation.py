"""Visoes versionadas de itens, resultados e manifesto de documentos PNCP."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import PncpDocumento, PncpEvento, PncpItem, PncpOportunidade, PncpRecursoVersao, PncpResultadoItem
from app.pncp.derived_status import aplicar_status_session, inferir_status_resultado_itens


def _hash(valor: Any) -> str:
    return hashlib.sha256(json.dumps(valor, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _numero(valor: Any, campo: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        raise ValueError(f"{campo} PNCP invalido")
    return valor


def _decimal_texto(valor: Any) -> str | None:
    return None if valor is None else str(valor)


def _data(valor: Any) -> date | None:
    return date.fromisoformat(str(valor)[:10]) if valor else None


def _versao(session: Session, oportunidade: PncpOportunidade, tipo: str, chave: str, revisao: int, hash_atual: str, status: str, payload: dict[str, Any]) -> None:
    session.execute(insert(PncpRecursoVersao).values(source=oportunidade.source, external_id=oportunidade.external_id,
        resource_type=tipo, resource_key=chave, revisao=revisao, payload_hash=hash_atual, status=status, payload=payload)
        .on_conflict_do_nothing(constraint="uq_pncp_recurso_versao"))


def _evento(session: Session, oportunidade: PncpOportunidade, tipo: str, chave: str, revisao: int, anterior: str | None, atual: str) -> None:
    chave_evento = _hash([tipo, oportunidade.source, oportunidade.external_id, chave, revisao, anterior, atual])
    session.execute(insert(PncpEvento).values(event_key=chave_evento, event_type=tipo, source=oportunidade.source,
        external_id=oportunidade.external_id, payload_hash=atual,
        details={"recurso": chave, "revisao": revisao, "before": anterior, "after": atual})
        .on_conflict_do_nothing(constraint="uq_pncp_eventos_event_key"))


def salvar_itens_session(session: Session, oportunidade: PncpOportunidade, itens: list[dict[str, Any]]) -> dict[str, int]:
    """Atualiza itens da contratacao em uma transacao; lote so existe se a fonte o identificar."""
    vistos: set[int] = set()
    contagem = {"novos": 0, "alterados": 0, "lotes_identificados": 0}
    for payload in itens:
        numero = _numero(payload.get("numeroItem"), "numeroItem")
        if numero in vistos:
            raise ValueError("numeroItem duplicado na resposta PNCP")
        vistos.add(numero)
        chave = {"source": oportunidade.source, "external_id": oportunidade.external_id, "numero_item": numero}
        atual = session.get(PncpItem, chave)
        novo_hash = _hash(payload)
        if atual is not None and atual.payload_hash == novo_hash:
            continue
        anterior = atual.payload_hash if atual else None
        if atual is None:
            atual = PncpItem(**chave, revisao=1)
            session.add(atual)
        else:
            atual.revisao += 1
        lote = payload.get("numeroLote")
        atual.numero_lote = str(lote) if lote is not None else None
        atual.descricao = payload.get("descricao")
        atual.quantidade = _decimal_texto(payload.get("quantidade"))
        atual.unidade = payload.get("unidadeMedida")
        atual.valor_unitario_estimado = _decimal_texto(payload.get("valorUnitarioEstimado"))
        atual.valor_total_estimado = _decimal_texto(payload.get("valorTotal"))
        atual.situacao = payload.get("situacaoCompraItemNome")
        atual.criterio_julgamento = payload.get("criterioJulgamentoNome")
        atual.payload_hash = novo_hash
        atual.atualizado_em = datetime.now(timezone.utc)
        _versao(session, oportunidade, "ITEM", str(numero), atual.revisao, novo_hash, "ATUAL", payload)
        _evento(session, oportunidade, "ITEM_NOVO" if anterior is None else "ITEM_ALTERADO", str(numero), atual.revisao, anterior, novo_hash)
        contagem["novos" if anterior is None else "alterados"] += 1
        contagem["lotes_identificados"] += lote is not None
    return contagem


def salvar_resultados_session(session: Session, oportunidade: PncpOportunidade, numero_item: int, resultados: list[dict[str, Any]], *, tem_resultado: bool | None = None) -> dict[str, int | str]:
    item = session.get(PncpItem, {"source": oportunidade.source, "external_id": oportunidade.external_id, "numero_item": numero_item})
    if item is None:
        raise ValueError("item ainda nao persistido")
    anteriores = {r.sequencial_resultado: r for r in session.scalars(select(PncpResultadoItem).where(
        PncpResultadoItem.source == oportunidade.source, PncpResultadoItem.external_id == oportunidade.external_id,
        PncpResultadoItem.numero_item == numero_item)).all()}
    vistos: set[int] = set()
    alterados = 0
    for payload in resultados:
        if payload.get("numeroItem") != numero_item:
            raise ValueError("resultado pertence a outro item")
        sequencial = _numero(payload.get("sequencialResultado"), "sequencialResultado")
        if sequencial in vistos:
            raise ValueError("sequencialResultado duplicado")
        vistos.add(sequencial)
        chave = {"source": oportunidade.source, "external_id": oportunidade.external_id, "numero_item": numero_item, "sequencial_resultado": sequencial}
        atual = session.get(PncpResultadoItem, chave)
        novo_hash = _hash(payload)
        if atual is not None and atual.payload_hash == novo_hash and atual.disponibilidade == "ATUAL":
            continue
        anterior = atual.payload_hash if atual else None
        if atual is None:
            atual = PncpResultadoItem(**chave, revisao=1)
            session.add(atual)
        else:
            atual.revisao += 1
        identificador = payload.get("niFornecedor")
        atual.fornecedor = payload.get("nomeRazaoSocialFornecedor")
        atual.cnpj_fornecedor = identificador if payload.get("tipoPessoa") == "PJ" and isinstance(identificador, str) and re.fullmatch(r"[A-Za-z0-9]{14}", identificador) else None
        atual.valor_unitario_homologado = _decimal_texto(payload.get("valorUnitarioHomologado"))
        atual.quantidade_homologada = _decimal_texto(payload.get("quantidadeHomologada"))
        atual.situacao = payload.get("situacaoCompraItemResultadoNome")
        atual.data_resultado = _data(payload.get("dataResultado"))
        atual.disponibilidade = "ATUAL"
        atual.payload_hash = novo_hash
        atual.atualizado_em = datetime.now(timezone.utc)
        _versao(session, oportunidade, "RESULTADO", f"{numero_item}:{sequencial}", atual.revisao, novo_hash, "ATUAL", payload)
        _evento(session, oportunidade, "RESULTADO_NOVO" if anterior is None else "RESULTADO_ALTERADO", f"{numero_item}:{sequencial}", atual.revisao, anterior, novo_hash)
        alterados += 1
    for sequencial, atual in anteriores.items():
        if sequencial in vistos or atual.disponibilidade == "REMOVIDO":
            continue
        atual.revisao += 1
        atual.disponibilidade = "REMOVIDO"
        atual.atualizado_em = datetime.now(timezone.utc)
        chave_recurso = f"{numero_item}:{sequencial}"
        _versao(session, oportunidade, "RESULTADO", chave_recurso, atual.revisao, atual.payload_hash,
            "REMOVIDO", {"sequencialResultado": sequencial, "numeroItem": numero_item})
        _evento(session, oportunidade, "RESULTADO_REMOVIDO", chave_recurso, atual.revisao,
            atual.payload_hash, _hash([atual.payload_hash, "REMOVIDO"]))
        alterados += 1
    if resultados:
        status = "FINAL" if item.situacao and "HOMOLOG" in item.situacao.upper() else "PARCIAL"
    else:
        # Ausência só é confirmada pela fonte; campo omitido/null permanece inconclusivo.
        status = "AUSENTE" if tem_resultado is False else "PARCIAL"
    if item.resultado_status != status:
        anterior_status = item.resultado_status or "NAO_CONSULTADO"
        item.revisao += 1
        item.resultado_status = status
        _versao(session, oportunidade, "ITEM_RESULTADO_STATUS", str(numero_item), item.revisao,
            _hash(status), status, {"status": status, "anterior": anterior_status})
        _evento(session, oportunidade, "RESULTADO_STATUS_ALTERADO", str(numero_item), item.revisao,
            _hash(anterior_status), _hash(status))
    estados = list(session.scalars(select(PncpItem.resultado_status).where(
        PncpItem.source == oportunidade.source, PncpItem.external_id == oportunidade.external_id)))
    geral = inferir_status_resultado_itens(estados)
    aplicar_status_session(session, oportunidade, agora=datetime.now(timezone.utc), resultado_estado=geral)
    return {"alterados": alterados, "status": status}


def salvar_documentos_session(session: Session, oportunidade: PncpOportunidade, documentos: list[dict[str, Any]], *, observacao: str | None = None) -> dict[str, int]:
    """Uma observacao valida e atomica; a primeira ausencia nao remove metadados."""
    if observacao is not None:
        registro = session.execute(insert(PncpRecursoVersao).values(source=oportunidade.source, external_id=oportunidade.external_id,
            resource_type="CONSULTA_DOCUMENTOS", resource_key=observacao, revisao=1, payload_hash=_hash(documentos), status="CONCLUIDA", payload={})
            .on_conflict_do_nothing(constraint="uq_pncp_recurso_versao").returning(PncpRecursoVersao.id)).scalar_one_or_none()
        if registro is None:
            return {"novos": 0, "alterados": 0, "temporarios": 0, "removidos": 0}
    anteriores = {r.sequencial_documento: r for r in session.scalars(select(PncpDocumento).where(
        PncpDocumento.source == oportunidade.source, PncpDocumento.external_id == oportunidade.external_id)).all()}
    vistos: set[int] = set()
    contagem = {"novos": 0, "alterados": 0, "temporarios": 0, "removidos": 0}
    for payload in documentos:
        numero = _numero(payload.get("sequencialDocumento"), "sequencialDocumento")
        url = payload.get("url")
        if numero in vistos or not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError("documento PNCP duplicado ou sem URL oficial")
        vistos.add(numero)
        atual = anteriores.get(numero)
        novo_hash = _hash(payload)
        anterior = atual.payload_hash if atual else None
        if atual is None:
            atual = PncpDocumento(source=oportunidade.source, external_id=oportunidade.external_id, sequencial_documento=numero, revisao=1)
            session.add(atual)
        mudou = anterior != novo_hash or atual.disponibilidade != "DISPONIVEL"
        if mudou and anterior is not None:
            atual.revisao += 1
        atual.titulo = payload.get("titulo")
        atual.tipo = payload.get("tipoDocumentoNome")
        atual.url_oficial = url
        atual.publicado_em = _data(payload.get("dataPublicacaoPncp"))
        atual.payload_hash = novo_hash
        atual.disponibilidade = "DISPONIVEL"
        atual.ausencias_consecutivas = 0
        if mudou:
            atual.atualizado_em = datetime.now(timezone.utc)
            _versao(session, oportunidade, "DOCUMENTO", str(numero), atual.revisao, novo_hash, "DISPONIVEL", payload)
            _evento(session, oportunidade, "DOCUMENTO_NOVO" if anterior is None else "DOCUMENTO_ALTERADO", str(numero), atual.revisao, anterior, novo_hash)
            contagem["novos" if anterior is None else "alterados"] += 1
    for numero, atual in anteriores.items():
        if numero in vistos or atual.disponibilidade == "REMOVIDO":
            continue
        atual.ausencias_consecutivas += 1
        status = "REMOVIDO" if atual.ausencias_consecutivas >= 2 else "TEMPORARIAMENTE_INDISPONIVEL"
        if status == atual.disponibilidade:
            continue
        atual.disponibilidade = status
        atual.revisao += 1
        atual.atualizado_em = datetime.now(timezone.utc)
        _versao(session, oportunidade, "DOCUMENTO", str(numero), atual.revisao, atual.payload_hash, status, {"url": atual.url_oficial})
        _evento(session, oportunidade, "DOCUMENTO_REMOVIDO" if status == "REMOVIDO" else "DOCUMENTO_INDISPONIVEL", str(numero), atual.revisao, atual.payload_hash, _hash([atual.payload_hash, status]))
        contagem["removidos" if status == "REMOVIDO" else "temporarios"] += 1
    return contagem
