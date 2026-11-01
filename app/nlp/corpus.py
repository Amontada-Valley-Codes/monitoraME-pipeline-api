"""Prepara corpus textual incremental a partir das visões normalizadas PNCP."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Iterator
from hashlib import sha256
import unicodedata
from typing import Any
from html.parser import HTMLParser

from sqlalchemy import delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.models import NlpProcurementSegment, PncpItem, PncpOportunidade


CORPUS_VERSION = "pncp-text-v1"
STATUS_READY = "READY"
STATUS_NO_USABLE_TEXT = "NO_USABLE_TEXT"
_LIMITE_PARAMETROS_SQL = 60_000
_CAMPOS_ESTRUTURADOS = (
    "valor_total_estimado",
    "valor_total_homologado",
    "orcamento_sigiloso",
    "orcamento_sigiloso_codigo",
    "orcamento_sigiloso_descricao",
    "data_abertura_proposta",
    "data_publicacao",
    "data_atualizacao",
    "data_atualizacao_global",
    "prazo_proposta_final_origem",
    "modalidade_codigo",
    "situacao_codigo",
    "instrumento_codigo",
    "forma_participacao_origem",
    "rule_version",
)


class _ExtratorTexto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_data(self, data: str) -> None:
        self.partes.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr", "td", "th"}:
            self.partes.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "tr", "td", "th"}:
            self.partes.append(" ")


@dataclass(frozen=True)
class SegmentoCorpus:
    source: str
    external_id: str
    segment_type: str
    segment_key: str
    source_field: str
    source_version_hash: str
    segment_version_hash: str | None
    text_hash: str | None
    normalized_text: str | None
    status: str
    item_id: int | None
    lot_id: str | None
    item_ids: list[int]
    structured_metadata: dict[str, Any]


@dataclass
class ResultadoSincronizacao:
    opportunities_scanned: int = 0
    segments_created: int = 0
    segments_updated: int = 0
    segments_unchanged: int = 0
    segments_without_text: int = 0
    stale_segments_deleted: int = 0


def normalizar_texto(valor: str | None) -> str | None:
    """Remove marcação/formatação e normaliza espaços sem descartar conteúdo textual."""
    if not isinstance(valor, str):
        return None
    parser = _ExtratorTexto()
    parser.feed(valor)
    parser.close()
    texto = " ".join(parser.partes)
    texto = unicodedata.normalize("NFKC", texto)
    texto = " ".join(texto.split())
    if len(texto) < 2 or not any(caractere.isalnum() for caractere in texto):
        return None
    return texto


def _hash_texto(texto: str | None) -> str | None:
    if texto is None:
        return None
    return sha256(texto.encode("utf-8")).hexdigest()


def _reutilizar_ou_normalizar(
    texto: str | None, atual: NlpProcurementSegment | None
) -> str | None:
    if atual is not None and atual.segment_version_hash == _hash_texto(texto):
        return atual.normalized_text
    return normalizar_texto(texto)


def _iso(valor: Any) -> Any:
    return valor.isoformat() if hasattr(valor, "isoformat") else valor


def _metadados_oportunidade(oportunidade: PncpOportunidade) -> dict[str, Any]:
    semanticos = oportunidade.dados_semanticos or {}
    return {
        "corpus_version": CORPUS_VERSION,
        "modality": oportunidade.modalidade,
        "situation": oportunidade.situacao,
        "official_status": oportunidade.official_status,
        "derived_status": oportunidade.derived_status,
        "published_at": _iso(oportunidade.data_publicacao),
        "proposal_deadline": _iso(oportunidade.prazo_proposta_final),
        "normalized_source_fields": {
            campo: semanticos[campo]
            for campo in _CAMPOS_ESTRUTURADOS
            if campo in semanticos
        },
        "data_quality_status": oportunidade.data_quality_status,
        "normalized_at": _iso(oportunidade.normalized_at),
        "source_updated_at": _iso(oportunidade.source_updated_at),
    }


def _segmento(
    oportunidade: PncpOportunidade,
    *,
    segment_type: str,
    segment_key: str,
    source_field: str,
    raw_text: str | None,
    segment_version_hash: str | None = None,
    item_id: int | None = None,
    lot_id: str | None = None,
    item_ids: list[int] | None = None,
    structured_metadata: dict[str, Any] | None = None,
    atual: NlpProcurementSegment | None = None,
) -> SegmentoCorpus:
    versao_texto = _hash_texto(raw_text)
    if atual is not None and atual.segment_version_hash == versao_texto:
        texto = atual.normalized_text
        text_hash = atual.text_hash
        status = atual.status
    else:
        texto = normalizar_texto(raw_text)
        text_hash = _hash_texto(texto)
        status = STATUS_READY if texto else STATUS_NO_USABLE_TEXT
    return SegmentoCorpus(
        source=oportunidade.source,
        external_id=oportunidade.external_id,
        segment_type=segment_type,
        segment_key=segment_key,
        source_field=source_field,
        source_version_hash=oportunidade.payload_hash,
        segment_version_hash=versao_texto,
        text_hash=text_hash,
        normalized_text=texto,
        status=status,
        item_id=item_id,
        lot_id=lot_id,
        item_ids=item_ids or [],
        structured_metadata={"corpus_version": CORPUS_VERSION, **(structured_metadata or {})},
    )


def preparar_segmentos(
    oportunidade: PncpOportunidade,
    itens: list[PncpItem],
    existentes: dict[tuple[str, str], NlpProcurementSegment] | None = None,
) -> list[SegmentoCorpus]:
    """Monta objeto, descrições de item e agrupamentos de itens por lote."""
    base_metadata = _metadados_oportunidade(oportunidade)
    segmentos = [
        _segmento(
            oportunidade,
            segment_type="OBJECT",
            segment_key="object",
            source_field="pncp_oportunidades.objeto",
            raw_text=oportunidade.objeto,
            structured_metadata=base_metadata,
            atual=(existentes or {}).get(("OBJECT", "object")),
        )
    ]
    descricao_complementar = (oportunidade.dados_semanticos or {}).get(
        "orcamento_sigiloso_descricao"
    )
    if isinstance(descricao_complementar, str):
        segmentos.append(
            _segmento(
                oportunidade,
                segment_type="COMPLEMENTARY",
                segment_key="complementary:orcamento_sigiloso_descricao",
                source_field="pncp_oportunidades.dados_semanticos.orcamento_sigiloso_descricao",
                raw_text=descricao_complementar,
                structured_metadata=base_metadata,
                atual=(existentes or {}).get(("COMPLEMENTARY", "complementary:orcamento_sigiloso_descricao")),
            )
        )
    itens_por_lote: dict[str, list[PncpItem]] = {}
    for item in itens:
        metadata = {
            "quantity": item.quantidade,
            "unit": item.unidade,
            "estimated_unit_value": item.valor_unitario_estimado,
            "estimated_total_value": item.valor_total_estimado,
            "situation": item.situacao,
            "award_criterion": item.criterio_julgamento,
            "updated_at": _iso(item.atualizado_em),
            "source_item_version_hash": item.payload_hash,
        }
        segmentos.append(
            _segmento(
                oportunidade,
                segment_type="ITEM",
                segment_key=f"item:{item.numero_item}",
                source_field="pncp_itens.descricao",
                raw_text=item.descricao,
                item_id=item.numero_item,
                lot_id=item.numero_lote,
                item_ids=[item.numero_item],
                structured_metadata=metadata,
                atual=(existentes or {}).get(("ITEM", f"item:{item.numero_item}")),
            )
        )
        if item.numero_lote is not None:
            itens_por_lote.setdefault(item.numero_lote, []).append(item)

    for numero_lote, itens_lote in sorted(itens_por_lote.items()):
        descricoes = [
            f"Item {item.numero_item}: {texto_item}"
            for item in itens_lote
            if (texto_item := _reutilizar_ou_normalizar(
                item.descricao,
                (existentes or {}).get(("ITEM", f"item:{item.numero_item}")),
            ))
        ]
        texto_lote = " ".join(descricoes) if descricoes else None
        segmentos.append(
            _segmento(
                oportunidade,
                segment_type="LOT",
                segment_key=f"lot:{numero_lote}",
                source_field="pncp_itens.descricao",
                raw_text=texto_lote,
                lot_id=numero_lote,
                item_ids=[item.numero_item for item in itens_lote],
                structured_metadata={
                    **base_metadata,
                    "lot_number": numero_lote,
                    "item_count": len(itens_lote),
                    "source_item_versions": [
                        {"item_id": item.numero_item, "payload_hash": item.payload_hash}
                        for item in itens_lote
                    ],
                },
                atual=(existentes or {}).get(("LOT", f"lot:{numero_lote}")),
            )
        )
    return segmentos


def _segmento_changed(atual: NlpProcurementSegment, novo: SegmentoCorpus) -> bool:
    return any(
        getattr(atual, campo) != getattr(novo, campo)
        for campo in (
            "source_field",
            "source_version_hash",
            "segment_version_hash",
            "text_hash",
            "normalized_text",
            "status",
            "item_id",
            "lot_id",
            "item_ids",
            "structured_metadata",
        )
    )


def _lotes[T](valores: list[T], tamanho: int) -> Iterator[list[T]]:
    for inicio in range(0, len(valores), tamanho):
        yield valores[inicio : inicio + tamanho]


def sincronizar_corpus(
    session: Session, *, page_size: int = 200, dry_run: bool = False,
    opportunity_keys: list[tuple[str, str]] | None = None,
) -> ResultadoSincronizacao:
    """Varre oportunidades em páginas e grava somente segmentos novos/alterados."""
    if not 1 <= page_size <= 2000:
        raise ValueError("page_size deve estar entre 1 e 2000")
    resultado = ResultadoSincronizacao()
    ultima_chave: tuple[str, str] | None = None
    if opportunity_keys is not None and not opportunity_keys:
        return resultado

    while True:
        consulta = select(PncpOportunidade).order_by(
            PncpOportunidade.source, PncpOportunidade.external_id
        ).limit(page_size)
        if opportunity_keys is not None:
            consulta = consulta.where(
                tuple_(PncpOportunidade.source, PncpOportunidade.external_id).in_(opportunity_keys)
            )
        if ultima_chave is not None:
            consulta = consulta.where(
                tuple_(PncpOportunidade.source, PncpOportunidade.external_id) > tuple_(*ultima_chave)
            )
        oportunidades = list(session.scalars(consulta))
        if not oportunidades:
            break

        chaves = [(o.source, o.external_id) for o in oportunidades]
        ultima_chave = chaves[-1]
        itens = list(
            session.scalars(
                select(PncpItem)
                .where(tuple_(PncpItem.source, PncpItem.external_id).in_(chaves))
                .order_by(PncpItem.source, PncpItem.external_id, PncpItem.numero_item)
            )
        )
        itens_por_oportunidade: dict[tuple[str, str], list[PncpItem]] = {}
        for item in itens:
            itens_por_oportunidade.setdefault((item.source, item.external_id), []).append(item)

        existentes = list(
            session.scalars(
                select(NlpProcurementSegment).where(
                    tuple_(NlpProcurementSegment.source, NlpProcurementSegment.external_id).in_(chaves)
                )
            )
        )
        existentes_por_oportunidade: dict[tuple[str, str], dict[tuple[str, str], NlpProcurementSegment]] = {}
        for atual in existentes:
            existentes_por_oportunidade.setdefault((atual.source, atual.external_id), {})[
                (atual.segment_type, atual.segment_key)
            ] = atual

        valores_para_gravar: list[dict[str, Any]] = []
        chaves_obsoletas: list[tuple[str, str, str, str]] = []
        for oportunidade in oportunidades:
            chave = (oportunidade.source, oportunidade.external_id)
            resultado.opportunities_scanned += 1
            atuais = existentes_por_oportunidade.get(chave, {})
            novos_segmentos = preparar_segmentos(
                oportunidade, itens_por_oportunidade.get(chave, []), atuais
            )
            chaves_novas = {(segmento.segment_type, segmento.segment_key) for segmento in novos_segmentos}
            for segmento in novos_segmentos:
                atual = atuais.get((segmento.segment_type, segmento.segment_key))
                if atual is None:
                    resultado.segments_created += 1
                elif _segmento_changed(atual, segmento):
                    resultado.segments_updated += 1
                else:
                    resultado.segments_unchanged += 1
                if segmento.status == STATUS_NO_USABLE_TEXT:
                    resultado.segments_without_text += 1
                valores_para_gravar.append(asdict(segmento))
            chaves_obsoletas.extend(
                (oportunidade.source, oportunidade.external_id, tipo, chave_segmento)
                for tipo, chave_segmento in atuais
                if (tipo, chave_segmento) not in chaves_novas
            )

        if not dry_run and valores_para_gravar:
            tabela = NlpProcurementSegment.__table__
            tamanho_lote = max(1, _LIMITE_PARAMETROS_SQL // len(tabela.columns))
            for lote in _lotes(valores_para_gravar, tamanho_lote):
                comando = insert(tabela).values(lote)
                atualizacao = {
                    campo: getattr(comando.excluded, campo)
                    for campo in (
                        "source_field",
                        "source_version_hash",
                        "segment_version_hash",
                        "text_hash",
                        "normalized_text",
                        "status",
                        "item_id",
                        "lot_id",
                        "item_ids",
                        "structured_metadata",
                    )
                }
                atualizacao["prepared_at"] = func.now()
                comando = comando.on_conflict_do_update(
                    index_elements=["source", "external_id", "segment_type", "segment_key"],
                    set_=atualizacao,
                    where=or_(
                        *(
                            getattr(tabela.c, campo).is_distinct_from(getattr(comando.excluded, campo))
                            for campo in atualizacao
                            if campo != "prepared_at"
                        )
                    ),
                )
                session.execute(comando)
        if not dry_run and chaves_obsoletas:
            tamanho_lote = max(1, _LIMITE_PARAMETROS_SQL // 4)
            for lote in _lotes(chaves_obsoletas, tamanho_lote):
                session.execute(
                    delete(NlpProcurementSegment).where(
                        tuple_(
                            NlpProcurementSegment.source,
                            NlpProcurementSegment.external_id,
                            NlpProcurementSegment.segment_type,
                            NlpProcurementSegment.segment_key,
                        ).in_(lote)
                    )
                )
            resultado.stale_segments_deleted += len(chaves_obsoletas)
        if not dry_run:
            session.flush()

    return resultado
