"""Persistência versionada de notas de pagamento do TCE-CE por competência."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Iterable

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.core import database, orm
from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.core.models import (
    TceDespesaIngestionRun,
    TceEmpenho,
    TceMunicipio,
    TcePagamento,
    TcePagamentoIngestionRun,
    TceOrgaoClassificacao,
)


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


def _competencias_entre(inicio: str, fim: str) -> list[str]:
    ano, mes = map(int, inicio.split("-"))
    fim_ano, fim_mes = map(int, fim.split("-"))
    competencias = []
    while (ano, mes) <= (fim_ano, fim_mes):
        competencias.append(f"{ano:04d}-{mes:02d}")
        if mes == 12:
            ano, mes = ano + 1, 1
        else:
            mes += 1
    return competencias


def listar_pagamentos_para_analise(
    *, codigo_municipio_tce: str, exercicio_orcamento: int,
    data_inicial: date, data_final: date,
    competencias_publicadas: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Le pagamentos publicados no periodo civil, para um orcamento independente."""
    database.init_db()
    if data_inicial > data_final:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")
    competencia_inicial = data_inicial.strftime("%Y-%m")
    competencia_final = data_final.strftime("%Y-%m")
    with orm.main_session() as session:
        runs = session.scalars(select(TcePagamentoIngestionRun).where(
            TcePagamentoIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TcePagamentoIngestionRun.exercicio_orcamento == exercicio_orcamento,
            TcePagamentoIngestionRun.status == STATUS_PUBLICADO,
        ).order_by(TcePagamentoIngestionRun.competencia)).all()
        competencias = _competencias_entre(competencia_inicial, competencia_final)
        por_competencia = {run.competencia: run for run in runs}
        ausentes = [item for item in competencias if item not in por_competencia]
        if ausentes:
            return [], ausentes
        if competencias_publicadas is not None:
            competencias_publicadas.extend(competencias)
        classificacoes = session.scalars(select(TceOrgaoClassificacao).where(
            TceOrgaoClassificacao.codigo_municipio_tce == codigo_municipio_tce,
            TceOrgaoClassificacao.exercicio_orcamento == exercicio_orcamento,
        )).all()
        executivo = {item.codigo_orgao for item in classificacoes
                     if item.classificacao == "EXECUTIVO_MUNICIPAL"}
        camara = {item.codigo_orgao for item in classificacoes if item.classificacao == "CAMARA"}
        if not executivo or not camara:
            raise ValueError("Classificacao de orgaos incompleta para calcular pagamentos da Prefeitura.")
        pagamentos_no_periodo = session.scalars(select(TcePagamento).where(
            TcePagamento.run_id.in_([run.id for run in runs]),
            TcePagamento.data_nota_pagamento >= data_inicial,
            TcePagamento.data_nota_pagamento <= data_final,
        )).all()
        orgaos_sem_classificacao = sorted({
            item.codigo_orgao for item in pagamentos_no_periodo
            if item.codigo_orgao not in executivo and item.codigo_orgao not in camara
        })
        if orgaos_sem_classificacao:
            raise ValueError(
                "Pagamentos com orgao sem classificacao cadastral: "
                f"{orgaos_sem_classificacao}."
            )
        estados_desconhecidos = sorted({
            str(item.estado_de_estornado) for item in pagamentos_no_periodo
            if str(item.estado_de_estornado) not in {"0", "1"}
        })
        if estados_desconhecidos:
            raise ValueError(
                "Pagamentos com estado de estorno desconhecido: "
                f"{estados_desconhecidos}."
            )
        pagamentos = [
            item for item in pagamentos_no_periodo
            if item.codigo_orgao in executivo and str(item.estado_de_estornado) == "0"
        ]
        pagamentos_unicos: dict[str, TcePagamento] = {}
        duplicadas_conflitantes: set[str] = set()
        for pagamento in pagamentos:
            anterior = pagamentos_unicos.get(pagamento.chave_pagamento)
            if anterior is None:
                pagamentos_unicos[pagamento.chave_pagamento] = pagamento
                continue
            campos_relevantes = (
                "chave_empenho",
                "data_nota_pagamento",
                "valor_nota_pagamento_centavos",
                "estado_de_estornado",
            )
            if any(
                getattr(anterior, campo) != getattr(pagamento, campo)
                for campo in campos_relevantes
            ):
                duplicadas_conflitantes.add(pagamento.chave_pagamento)
        if duplicadas_conflitantes:
            raise ValueError(
                "Chaves de pagamento duplicadas com conteudo conflitante: "
                f"{sorted(duplicadas_conflitantes)[:10]} "
                f"(total={len(duplicadas_conflitantes)})."
            )
        pagamentos = list(pagamentos_unicos.values())
        chaves = {item.chave_empenho for item in pagamentos}
        empenhos = session.execute(select(
            TceEmpenho,
            TceDespesaIngestionRun.competencia.label("competencia_empenho"),
        ).join(
            TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id,
        ).where(
            TceDespesaIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TceDespesaIngestionRun.status == STATUS_PUBLICADO,
            TceEmpenho.exercicio_orcamento == exercicio_orcamento,
            TceEmpenho.chave_empenho.in_(chaves),
        ).order_by(TceDespesaIngestionRun.competencia)).all() if chaves else []
        indice: dict[str, tuple[TceEmpenho, str]] = {}
        for empenho, competencia in empenhos:
            # Um empenho pode reaparecer em snapshots mensais; sua referencia
            # de origem e a primeira competencia documental publicada.
            anterior = indice.get(empenho.chave_empenho)
            if anterior is not None:
                empenho_anterior = anterior[0]
                campos = (
                    "codigo_elemento_despesa", "cnpj_fornecedor",
                    "documento_fornecedor", "tipo_documento_fornecedor",
                    "valor_empenhado_centavos",
                )
                if any(getattr(empenho_anterior, campo) != getattr(empenho, campo)
                       for campo in campos):
                    raise ValueError(
                        "Chave de empenho duplicada com dimensoes conflitantes: "
                        f"{empenho.chave_empenho}."
                    )
            else:
                indice[empenho.chave_empenho] = (empenho, competencia)
        orfas = sorted({
            pagamento.chave_pagamento for pagamento in pagamentos
            if pagamento.chave_empenho not in indice
        })
        if orfas:
            raise ValueError(
                "Pagamentos sem empenho publicado correspondente: "
                f"{orfas[:10]} (total={len(orfas)})."
            )
        resultado = []
        for pagamento in pagamentos:
            empenho, _competencia_empenho = indice[pagamento.chave_empenho]
            if (
                str(empenho.tipo_documento_fornecedor or "").strip() != "1"
                or not empenho.cnpj_fornecedor
            ):
                # O recorte validado pelo projeto ``dados`` é de credores CNPJ.
                # Falta de cadastro empresarial não exclui o pagamento; apenas
                # documentos que não identificam um CNPJ válido ficam fora.
                continue
            elemento = str(empenho.codigo_elemento_despesa or "")
            codigo_elemento = elemento[4:6] if len(elemento) == 8 else elemento
            if codigo_elemento not in NATUREZAS_DESPESA_MONITORADAS:
                continue
            competencia_pagamento = pagamento.data_nota_pagamento.strftime("%Y-%m")
            resultado.append({
                "exercicio_orcamento": exercicio_orcamento,
                "run_id": pagamento.run_id,
                "chave_pagamento": pagamento.chave_pagamento,
                "chave_empenho": pagamento.chave_empenho,
                "data_referencia": pagamento.data_nota_pagamento.isoformat(),
                "ano_mes": competencia_pagamento,
                "data_referencia_doc_pagamento": pagamento.data_referencia_doc,
                "data_nota_pagamento": pagamento.data_nota_pagamento.isoformat(),
                "valor_centavos": int(pagamento.valor_nota_pagamento_centavos),
                "natureza_despesa_codigo": codigo_elemento,
                "cnpj_fornecedor": empenho.cnpj_fornecedor,
                "documento_fornecedor": empenho.documento_fornecedor,
                "tipo_documento_fornecedor": empenho.tipo_documento_fornecedor,
                "nome_fornecedor": empenho.nome_fornecedor,
                "codigo_municipio_tce": codigo_municipio_tce,
            })
    return resultado, []


def listar_pagamentos_municipais_para_analise(
    *, codigo_municipio_tce: str, data_inicial: date, data_final: date,
    exercicio_orcamento: int | None = None,
    competencias_publicadas: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Combina exercícios orçamentários publicados no período de pagamento."""
    if data_inicial > data_final:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")
    database.init_db()
    with orm.main_session() as session:
        consulta_exercicios = select(TcePagamentoIngestionRun.exercicio_orcamento).where(
            TcePagamentoIngestionRun.codigo_municipio_tce == codigo_municipio_tce,
            TcePagamentoIngestionRun.status == STATUS_PUBLICADO,
        )
        if exercicio_orcamento is not None:
            exercicio_tce = (
                exercicio_orcamento * 100
                if exercicio_orcamento < 10_000
                else exercicio_orcamento
            )
            consulta_exercicios = consulta_exercicios.where(
                TcePagamentoIngestionRun.exercicio_orcamento == exercicio_tce
            )
        exercicios = session.execute(
            consulta_exercicios.distinct().order_by(
                TcePagamentoIngestionRun.exercicio_orcamento
            )
        ).scalars().all()
    if not exercicios:
        ausentes = []
        cursor = date(data_inicial.year, data_inicial.month, 1)
        fim = date(data_final.year, data_final.month, 1)
        while cursor <= fim:
            ausentes.append({"exercicio_orcamento": "NAO_PUBLICADO", "competencia": cursor.strftime("%Y-%m")})
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        return [], ausentes
    registros = []
    ausentes = []
    for exercicio in exercicios:
        pagamentos, faltantes = listar_pagamentos_para_analise(
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio,
            data_inicial=data_inicial,
            data_final=data_final,
            competencias_publicadas=competencias_publicadas,
        )
        registros.extend(pagamentos)
        ausentes.extend({"exercicio_orcamento": str(exercicio), "competencia": item}
                        for item in faltantes)
    return registros, ausentes


def listar_pagamentos_estaduais_para_analise(
    *, data_inicial: date, data_final: date, uf: str,
    competencias_publicadas: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Lista pagamentos estaduais somente quando todos os lotes municipais existem."""
    from app.core.models import IbgeMunicipio

    database.init_db()
    if data_inicial > data_final:
        raise ValueError("data_inicial deve ser menor ou igual a data_final.")
    with orm.main_session() as session:
        municipios = session.execute(select(TceMunicipio.codigo_municipio_tce).join(
            IbgeMunicipio,
            IbgeMunicipio.codigo_municipio == TceMunicipio.codigo_municipio_ibge,
        ).where(IbgeMunicipio.uf == uf.upper()).order_by(TceMunicipio.codigo_municipio_tce)).scalars().all()
    if not municipios:
        raise ValueError(f"Nenhum municipio TCE cadastrado para UF {uf}.")
    registros = []
    ausentes = []
    for codigo in municipios:
        pagamentos, faltantes = listar_pagamentos_municipais_para_analise(
            codigo_municipio_tce=str(codigo), data_inicial=data_inicial, data_final=data_final,
            competencias_publicadas=competencias_publicadas,
        )
        registros.extend(pagamentos)
        ausentes.extend({"codigo_municipio": str(codigo), **item} for item in faltantes)
    return registros, ausentes


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
        anterior_id = anterior.id if anterior is not None else None
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
        if anterior_id is not None:
            substituido_em = agora
            expiracao = agora + RETENCAO_LOTES_SUBSTITUIDOS
            # Libera a unicidade parcial imediatamente antes de publicar o novo snapshot.
            session.execute(
                update(TcePagamentoIngestionRun)
                .where(
                    TcePagamentoIngestionRun.id == anterior_id,
                    TcePagamentoIngestionRun.status == STATUS_PUBLICADO,
                )
                .values(
                    status=STATUS_SUBSTITUIDO,
                    substituido_em=substituido_em,
                    expira_em=expiracao,
                )
            )
            anterior.status = STATUS_SUBSTITUIDO
            anterior.substituido_em = substituido_em
            anterior.expira_em = expiracao
            session.flush()
        run.status = STATUS_PUBLICADO
        run.finalizado_em = agora
        run.publicado_em = agora
        session.flush()
        return ResultadoPublicacaoPagamentos(
            run.id, run.status, codigo_municipio_tce, exercicio_orcamento * 100,
            competencia, len(registros), [], anterior_id,
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
