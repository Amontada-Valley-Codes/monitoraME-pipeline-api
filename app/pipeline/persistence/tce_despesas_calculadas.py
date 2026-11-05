"""Persistencia versionada dos fatos calculados de despesas do TCE-CE."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any, Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.models import TceDespesaCalculada, TceDespesaCalculadaRun


STATUS_PUBLICADO = "PUBLICADO"
STATUS_SUBSTITUIDO = "SUBSTITUIDO"
NATUREZA_EMPENHO = "EMPENHO"
NATUREZA_ANULACAO = "ANULACAO_EMPENHO"
NATUREZA_PAGAMENTO = "PAGAMENTO"
NATUREZAS = {NATUREZA_EMPENHO, NATUREZA_ANULACAO, NATUREZA_PAGAMENTO}
PORTES = {"MEI", "ME", "EPP", "DEMAIS", "NAO_IDENTIFICADO"}
COMPETENCIA_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
logger = logging.getLogger(__name__)


class ContratoDespesaCalculadaInvalido(ValueError):
    """O resultado da Analise de Dados nao satisfaz o contrato persistido."""


@dataclass(frozen=True)
class ResultadoPublicacaoCalculo:
    run_id: int
    status: str
    escopo_chave: str
    input_hash: str
    algoritmo_versao: str
    quantidade_empenhos: int
    quantidade_anulacoes: int
    quantidade_pagamentos: int
    reutilizado: bool
    run_anterior_id: int | None = None


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _texto(valor: Any, campo: str, *, obrigatorio: bool = True) -> str | None:
    texto = "" if valor is None else str(valor).strip()
    if obrigatorio and not texto:
        raise ContratoDespesaCalculadaInvalido(f"Campo obrigatorio ausente: {campo}.")
    return texto or None


def _inteiro_nao_negativo(valor: Any, campo: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise ContratoDespesaCalculadaInvalido(
            f"{campo} deve ser inteiro nao negativo em centavos."
        )
    return valor


def _inteiro_opcional(valor: Any, campo: str) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ContratoDespesaCalculadaInvalido(f"{campo} deve ser inteiro.")
    try:
        return int(valor)
    except (TypeError, ValueError) as error:
        raise ContratoDespesaCalculadaInvalido(f"{campo} deve ser inteiro.") from error


def _data(valor: Any, campo: str) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except (TypeError, ValueError) as error:
        raise ContratoDespesaCalculadaInvalido(
            f"{campo} deve usar o formato YYYY-MM-DD."
        ) from error


def _data_hora_opcional(valor: Any, campo: str) -> datetime | None:
    if valor is None or valor == "":
        return None
    try:
        resultado = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))
    except (TypeError, ValueError) as error:
        raise ContratoDespesaCalculadaInvalido(f"{campo} deve ser ISO-8601.") from error
    if resultado.tzinfo is None:
        raise ContratoDespesaCalculadaInvalido(f"{campo} deve conter fuso horario.")
    return resultado


def _competencia(valor: Any, campo: str) -> str:
    resultado = _texto(valor, campo)
    assert resultado is not None
    if not COMPETENCIA_PATTERN.fullmatch(resultado):
        raise ContratoDespesaCalculadaInvalido(f"{campo} deve usar o formato YYYY-MM.")
    return resultado


def _json_normalizado(valor: Any) -> Any:
    return json.loads(
        json.dumps(
            valor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: item.isoformat()
            if isinstance(item, (date, datetime))
            else str(item),
        )
    )


def _hash(valor: Any) -> str:
    serializado = json.dumps(
        _json_normalizado(valor),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _normalizar_fontes(valor: dict[str, Any]) -> dict[str, Any]:
    """Canonicaliza metadados; listas de fontes representam conjuntos."""
    def normalizar(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(chave): normalizar(conteudo) for chave, conteudo in item.items()}
        if isinstance(item, list):
            itens = [normalizar(conteudo) for conteudo in item]
            return sorted(
                itens,
                key=lambda conteudo: json.dumps(
                    conteudo,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        return item

    resultado = normalizar(_json_normalizado(valor))
    assert isinstance(resultado, dict)
    return resultado


def _escopo(codigo_municipio_tce: str, exercicio_orcamento: int) -> str:
    return f"TCE-CE:{codigo_municipio_tce}:{exercicio_orcamento}"


def _normalizar_registro(
    registro: dict[str, Any],
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
) -> dict[str, Any]:
    natureza = _texto(registro.get("natureza"), "natureza")
    if natureza not in NATUREZAS:
        raise ContratoDespesaCalculadaInvalido(f"Natureza de despesa invalida: {natureza!r}.")

    municipio_registro = _texto(
        registro.get("codigo_municipio_tce"), "codigo_municipio_tce"
    )
    if municipio_registro != codigo_municipio_tce:
        raise ContratoDespesaCalculadaInvalido("Registro pertence a outro municipio.")

    exercicio_registro = _inteiro_opcional(
        registro.get("exercicio_orcamento"), "exercicio_orcamento"
    )
    if exercicio_registro != exercicio_orcamento:
        raise ContratoDespesaCalculadaInvalido("Registro pertence a outro exercicio.")

    porte = _texto(registro.get("porte_fornecedor"), "porte_fornecedor")
    if porte not in PORTES:
        raise ContratoDespesaCalculadaInvalido(f"Porte de fornecedor invalido: {porte!r}.")

    atributos = registro.get("atributos") or {}
    if not isinstance(atributos, dict):
        raise ContratoDespesaCalculadaInvalido("atributos deve ser um objeto JSON.")

    normalizado: dict[str, Any] = {
        "natureza": natureza,
        "chave_registro": _texto(registro.get("chave_registro"), "chave_registro"),
        "chave_empenho": _texto(registro.get("chave_empenho"), "chave_empenho"),
        "fonte": _texto(registro.get("fonte") or "TCE-CE", "fonte"),
        "source_run_id": _inteiro_opcional(registro.get("source_run_id"), "source_run_id"),
        "source_record_id": _inteiro_opcional(
            registro.get("source_record_id"), "source_record_id"
        ),
        "competencia_documento": _competencia(
            registro.get("competencia_documento"), "competencia_documento"
        ),
        "competencia_analitica": _competencia(
            registro.get("competencia_analitica"), "competencia_analitica"
        ),
        "data_referencia": _data(registro.get("data_referencia"), "data_referencia"),
        "exercicio_orcamento": exercicio_orcamento,
        "codigo_municipio_tce": codigo_municipio_tce,
        "codigo_municipio_ibge": _texto(
            registro.get("codigo_municipio_ibge"),
            "codigo_municipio_ibge",
            obrigatorio=False,
        ),
        "municipio": _texto(registro.get("municipio"), "municipio"),
        "uf": _texto(registro.get("uf"), "uf"),
        "codigo_orgao": _texto(registro.get("codigo_orgao"), "codigo_orgao"),
        "nome_orgao": _texto(
            registro.get("nome_orgao"), "nome_orgao", obrigatorio=False
        ),
        "codigo_unidade_orcamentaria": _texto(
            registro.get("codigo_unidade_orcamentaria"),
            "codigo_unidade_orcamentaria",
        ),
        "tipo_documento_fornecedor": _texto(
            registro.get("tipo_documento_fornecedor"),
            "tipo_documento_fornecedor",
            obrigatorio=False,
        ),
        "documento_fornecedor": _texto(
            registro.get("documento_fornecedor"),
            "documento_fornecedor",
            obrigatorio=False,
        ),
        "cnpj_fornecedor": _texto(
            registro.get("cnpj_fornecedor"), "cnpj_fornecedor", obrigatorio=False
        ),
        "cpf_fornecedor": _texto(
            registro.get("cpf_fornecedor"), "cpf_fornecedor", obrigatorio=False
        ),
        "nome_fornecedor": _texto(
            registro.get("nome_fornecedor"), "nome_fornecedor", obrigatorio=False
        ),
        "municipio_fornecedor": _texto(
            registro.get("municipio_fornecedor"),
            "municipio_fornecedor",
            obrigatorio=False,
        ),
        "uf_fornecedor": _texto(
            registro.get("uf_fornecedor"), "uf_fornecedor", obrigatorio=False
        ),
        "porte_fornecedor": porte,
        "fonte_porte": _texto(
            registro.get("fonte_porte"), "fonte_porte", obrigatorio=False
        ),
        "porte_observado_em": _data_hora_opcional(
            registro.get("porte_observado_em"), "porte_observado_em"
        ),
        "natureza_despesa_codigo": _texto(
            registro.get("natureza_despesa_codigo"), "natureza_despesa_codigo"
        ),
        "natureza_despesa_descricao": _texto(
            registro.get("natureza_despesa_descricao"),
            "natureza_despesa_descricao",
            obrigatorio=False,
        ),
        "valor_centavos": _inteiro_nao_negativo(
            registro.get("valor_centavos"), "valor_centavos"
        ),
        "atributos": _json_normalizado(atributos),
    }

    if normalizado["uf"] is not None:
        normalizado["uf"] = normalizado["uf"].upper()
    if normalizado["uf_fornecedor"] is not None:
        normalizado["uf_fornecedor"] = normalizado["uf_fornecedor"].upper()

    campos_calculo = (
        "valor_empenhado_centavos",
        "valor_anulado_centavos",
        "valor_liquido_centavos",
    )
    if natureza == NATUREZA_EMPENHO:
        for campo in campos_calculo:
            normalizado[campo] = _inteiro_nao_negativo(registro.get(campo), campo)
        if normalizado["chave_registro"] != normalizado["chave_empenho"]:
            raise ContratoDespesaCalculadaInvalido(
                "Empenho deve usar a propria chave como chave_registro."
            )
        if (
            normalizado["valor_empenhado_centavos"]
            - normalizado["valor_anulado_centavos"]
            != normalizado["valor_liquido_centavos"]
        ):
            raise ContratoDespesaCalculadaInvalido(
                "Valor liquido deve ser empenhado menos anulado."
            )
        if normalizado["valor_centavos"] != normalizado["valor_liquido_centavos"]:
            raise ContratoDespesaCalculadaInvalido(
                "valor_centavos do empenho deve ser o valor liquido calculado."
            )
    else:
        for campo in campos_calculo:
            if registro.get(campo) is not None:
                raise ContratoDespesaCalculadaInvalido(
                    f"{campo} so pode ser preenchido em EMPENHO."
                )
            normalizado[campo] = None

    conteudo_hash = _hash(normalizado)
    normalizado["conteudo_hash"] = conteudo_hash
    return normalizado


def validar_resultados_calculados(
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
    registros: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normaliza o contrato e valida identidades, valores e vinculos."""
    municipio = _texto(codigo_municipio_tce, "codigo_municipio_tce")
    assert municipio is not None
    if exercicio_orcamento < 10000 or exercicio_orcamento % 100 != 0:
        raise ContratoDespesaCalculadaInvalido(
            "exercicio_orcamento deve usar o formato AAAA00 do SIM/TCE-CE."
        )

    normalizados = [
        _normalizar_registro(
            dict(registro),
            codigo_municipio_tce=municipio,
            exercicio_orcamento=exercicio_orcamento,
        )
        for registro in registros
    ]
    identidades: set[tuple[str, str]] = set()
    empenhos: dict[str, dict[str, Any]] = {}
    for registro in normalizados:
        identidade = (registro["natureza"], registro["chave_registro"])
        if identidade in identidades:
            raise ContratoDespesaCalculadaInvalido(
                f"Registro calculado duplicado: {identidade[0]}:{identidade[1]}."
            )
        identidades.add(identidade)
        if registro["natureza"] == NATUREZA_EMPENHO:
            empenhos[registro["chave_empenho"]] = registro

    dimensoes_vinculo = (
        "codigo_municipio_tce",
        "exercicio_orcamento",
        "codigo_orgao",
        "codigo_unidade_orcamentaria",
        "natureza_despesa_codigo",
        "tipo_documento_fornecedor",
        "documento_fornecedor",
    )
    for registro in normalizados:
        if registro["natureza"] == NATUREZA_EMPENHO:
            continue
        empenho = empenhos.get(registro["chave_empenho"])
        if empenho is None:
            raise ContratoDespesaCalculadaInvalido(
                f"{registro['natureza']} sem empenho calculado correspondente: "
                f"{registro['chave_empenho']}."
            )
        divergentes = [
            campo for campo in dimensoes_vinculo if registro[campo] != empenho[campo]
        ]
        if divergentes:
            raise ContratoDespesaCalculadaInvalido(
                "Dimensoes do registro divergem do empenho vinculado: "
                + ", ".join(divergentes)
                + "."
            )

    return sorted(
        normalizados,
        key=lambda item: (item["natureza"], item["chave_registro"]),
    )


def _trancar_escopo(session: Session, escopo_chave: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:escopo))"),
        {"escopo": f"tce-despesas-calculadas:{escopo_chave}"},
    )


def _run_publicado(session: Session, escopo_chave: str) -> TceDespesaCalculadaRun | None:
    return session.scalar(
        select(TceDespesaCalculadaRun)
        .where(
            TceDespesaCalculadaRun.escopo_chave == escopo_chave,
            TceDespesaCalculadaRun.status == STATUS_PUBLICADO,
        )
        .with_for_update()
    )


def _modelo_registro(
    *,
    run_id: int,
    registro: dict[str, Any],
    registro_pai_id: int | None,
) -> TceDespesaCalculada:
    return TceDespesaCalculada(
        run_id=run_id,
        registro_pai_id=registro_pai_id,
        **registro,
    )


def publicar_resultados_calculados(
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
    algoritmo_versao: str,
    registros: Iterable[dict[str, Any]],
    fontes: dict[str, Any] | None = None,
    iniciado_em: datetime | None = None,
) -> ResultadoPublicacaoCalculo:
    """Publica atomicamente um snapshot calculado completo e preserva o anterior."""
    versao = _texto(algoritmo_versao, "algoritmo_versao")
    assert versao is not None
    normalizados = validar_resultados_calculados(
        codigo_municipio_tce=codigo_municipio_tce,
        exercicio_orcamento=exercicio_orcamento,
        registros=registros,
    )
    fontes_normalizadas = _normalizar_fontes(fontes or {})
    input_hash = _hash(
        {
            "fontes": fontes_normalizadas,
            "registros": normalizados,
        }
    )
    escopo_chave = _escopo(codigo_municipio_tce, exercicio_orcamento)
    contagens = Counter(item["natureza"] for item in normalizados)
    inicio = iniciado_em or _agora()
    agora = _agora()

    database.init_db()
    with orm.main_session() as session:
        _trancar_escopo(session, escopo_chave)
        anterior = _run_publicado(session, escopo_chave)
        if (
            anterior is not None
            and anterior.input_hash == input_hash
            and anterior.algoritmo_versao == versao
        ):
            logger.info(
                "Resultado calculado do TCE-CE reutilizado",
                extra={
                    "run_id": anterior.id,
                    "scope_key": escopo_chave,
                    "algorithm_version": versao,
                    "input_hash": input_hash,
                },
            )
            return ResultadoPublicacaoCalculo(
                run_id=anterior.id,
                status=anterior.status,
                escopo_chave=escopo_chave,
                input_hash=input_hash,
                algoritmo_versao=versao,
                quantidade_empenhos=anterior.quantidade_empenhos,
                quantidade_anulacoes=anterior.quantidade_anulacoes,
                quantidade_pagamentos=anterior.quantidade_pagamentos,
                reutilizado=True,
                run_anterior_id=anterior.run_anterior_id,
            )

        anterior_id = anterior.id if anterior is not None else None
        if anterior is not None:
            anterior.status = STATUS_SUBSTITUIDO
            anterior.substituido_em = agora
            session.flush()

        run = TceDespesaCalculadaRun(
            escopo_chave=escopo_chave,
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio_orcamento,
            algoritmo_versao=versao,
            input_hash=input_hash,
            status=STATUS_PUBLICADO,
            run_anterior_id=anterior_id,
            quantidade_empenhos=contagens[NATUREZA_EMPENHO],
            quantidade_anulacoes=contagens[NATUREZA_ANULACAO],
            quantidade_pagamentos=contagens[NATUREZA_PAGAMENTO],
            fontes=fontes_normalizadas,
            iniciado_em=inicio,
            publicado_em=agora,
        )
        session.add(run)
        session.flush()

        pais: dict[str, TceDespesaCalculada] = {}
        modelos_empenhos = []
        for registro in normalizados:
            if registro["natureza"] != NATUREZA_EMPENHO:
                continue
            modelo = _modelo_registro(
                run_id=run.id,
                registro=registro,
                registro_pai_id=None,
            )
            pais[registro["chave_empenho"]] = modelo
            modelos_empenhos.append(modelo)
        session.add_all(modelos_empenhos)
        session.flush()

        modelos_filhos = [
            _modelo_registro(
                run_id=run.id,
                registro=registro,
                registro_pai_id=pais[registro["chave_empenho"]].id,
            )
            for registro in normalizados
            if registro["natureza"] != NATUREZA_EMPENHO
        ]
        session.add_all(modelos_filhos)
        session.flush()

        logger.info(
            "Resultado calculado do TCE-CE publicado",
            extra={
                "run_id": run.id,
                "previous_run_id": anterior_id,
                "scope_key": escopo_chave,
                "algorithm_version": versao,
                "input_hash": input_hash,
                "commitment_count": run.quantidade_empenhos,
                "cancellation_count": run.quantidade_anulacoes,
                "payment_count": run.quantidade_pagamentos,
            },
        )
        return ResultadoPublicacaoCalculo(
            run_id=run.id,
            status=run.status,
            escopo_chave=escopo_chave,
            input_hash=input_hash,
            algoritmo_versao=versao,
            quantidade_empenhos=run.quantidade_empenhos,
            quantidade_anulacoes=run.quantidade_anulacoes,
            quantidade_pagamentos=run.quantidade_pagamentos,
            reutilizado=False,
            run_anterior_id=anterior_id,
        )


def _serializar(registro: Any) -> dict[str, Any]:
    resultado = dict(registro)
    for campo in ("data_referencia", "porte_observado_em", "publicado_em"):
        valor = resultado.get(campo)
        if valor is not None:
            resultado[campo] = valor.isoformat()
    for campo in (
        "valor_centavos",
        "valor_empenhado_centavos",
        "valor_anulado_centavos",
        "valor_liquido_centavos",
    ):
        if resultado.get(campo) is not None:
            resultado[campo] = int(resultado[campo])
    return resultado


def listar_resultados_calculados(
    *,
    codigo_municipio_tce: str | None = None,
    codigo_municipio_ibge: str | None = None,
    exercicio_orcamento: int | None = None,
    competencia_inicial: str | None = None,
    competencia_final: str | None = None,
    naturezas: Iterable[str] | None = None,
    portes: Iterable[str] | None = None,
    cnpj_fornecedor: str | None = None,
    tipo_documento_fornecedor: str | None = None,
    documento_fornecedor: str | None = None,
    codigo_orgao: str | None = None,
    naturezas_despesa: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Consulta somente snapshots calculados vigentes, sem efeitos externos."""
    filtros = [TceDespesaCalculadaRun.status == STATUS_PUBLICADO]
    if codigo_municipio_tce is not None:
        filtros.append(
            TceDespesaCalculadaRun.codigo_municipio_tce == codigo_municipio_tce
        )
    if codigo_municipio_ibge is not None:
        filtros.append(TceDespesaCalculada.codigo_municipio_ibge == codigo_municipio_ibge)
    if exercicio_orcamento is not None:
        filtros.append(
            TceDespesaCalculadaRun.exercicio_orcamento == exercicio_orcamento
        )
    if competencia_inicial is not None:
        filtros.append(
            TceDespesaCalculada.competencia_analitica
            >= _competencia(competencia_inicial, "competencia_inicial")
        )
    if competencia_final is not None:
        filtros.append(
            TceDespesaCalculada.competencia_analitica
            <= _competencia(competencia_final, "competencia_final")
        )
    naturezas_filtro = set(naturezas or [])
    if not naturezas_filtro.issubset(NATUREZAS):
        raise ContratoDespesaCalculadaInvalido("Filtro de natureza invalido.")
    if naturezas_filtro:
        filtros.append(TceDespesaCalculada.natureza.in_(sorted(naturezas_filtro)))
    portes_filtro = set(portes or [])
    if not portes_filtro.issubset(PORTES):
        raise ContratoDespesaCalculadaInvalido("Filtro de porte invalido.")
    if portes_filtro:
        filtros.append(TceDespesaCalculada.porte_fornecedor.in_(sorted(portes_filtro)))
    if cnpj_fornecedor is not None:
        filtros.append(TceDespesaCalculada.cnpj_fornecedor == cnpj_fornecedor)
    if tipo_documento_fornecedor is not None:
        filtros.append(
            TceDespesaCalculada.tipo_documento_fornecedor
            == tipo_documento_fornecedor
        )
    if documento_fornecedor is not None:
        filtros.append(
            TceDespesaCalculada.documento_fornecedor == documento_fornecedor
        )
    if codigo_orgao is not None:
        filtros.append(TceDespesaCalculada.codigo_orgao == codigo_orgao)
    naturezas_despesa_filtro = {
        _texto(item, "naturezas_despesa") for item in naturezas_despesa or []
    }
    if naturezas_despesa_filtro:
        filtros.append(
            TceDespesaCalculada.natureza_despesa_codigo.in_(
                sorted(naturezas_despesa_filtro)
            )
        )

    consulta = (
        select(
            TceDespesaCalculada,
            TceDespesaCalculadaRun.algoritmo_versao,
            TceDespesaCalculadaRun.input_hash,
            TceDespesaCalculadaRun.publicado_em,
        )
        .join(TceDespesaCalculadaRun, TceDespesaCalculada.run_id == TceDespesaCalculadaRun.id)
        .where(*filtros)
        .order_by(
            TceDespesaCalculada.competencia_analitica,
            TceDespesaCalculada.natureza,
            TceDespesaCalculada.chave_registro,
        )
    )
    database.init_db()
    with orm.main_session() as session:
        linhas = session.execute(consulta).all()
    return [
        _serializar(
            {
                **{
                    coluna.name: getattr(registro, coluna.name)
                    for coluna in TceDespesaCalculada.__table__.columns
                },
                "algoritmo_versao": algoritmo_versao,
                "input_hash": input_hash,
                "publicado_em": publicado_em,
            }
        )
        for registro, algoritmo_versao, input_hash, publicado_em in linhas
    ]


def listar_historico_registro(
    *,
    natureza: str,
    chave_registro: str,
) -> list[dict[str, Any]]:
    """Retorna todas as versoes de um fato, inclusive as substituidas."""
    if natureza not in NATUREZAS:
        raise ContratoDespesaCalculadaInvalido("Natureza de despesa invalida.")
    consulta = (
        select(
            TceDespesaCalculada,
            TceDespesaCalculadaRun.status.label("run_status"),
            TceDespesaCalculadaRun.algoritmo_versao,
            TceDespesaCalculadaRun.input_hash,
            TceDespesaCalculadaRun.publicado_em,
        )
        .join(TceDespesaCalculadaRun, TceDespesaCalculada.run_id == TceDespesaCalculadaRun.id)
        .where(
            TceDespesaCalculada.natureza == natureza,
            TceDespesaCalculada.chave_registro == chave_registro,
        )
        .order_by(TceDespesaCalculadaRun.publicado_em)
    )
    database.init_db()
    with orm.main_session() as session:
        linhas = session.execute(consulta).all()
    return [
        _serializar(
            {
                **{
                    coluna.name: getattr(registro, coluna.name)
                    for coluna in TceDespesaCalculada.__table__.columns
                },
                "run_status": run_status,
                "algoritmo_versao": algoritmo_versao,
                "input_hash": input_hash,
                "publicado_em": publicado_em,
            }
        )
        for registro, run_status, algoritmo_versao, input_hash, publicado_em in linhas
    ]


__all__ = [
    "ContratoDespesaCalculadaInvalido",
    "NATUREZA_ANULACAO",
    "NATUREZA_EMPENHO",
    "NATUREZA_PAGAMENTO",
    "ResultadoPublicacaoCalculo",
    "listar_historico_registro",
    "listar_resultados_calculados",
    "publicar_resultados_calculados",
    "validar_resultados_calculados",
]
