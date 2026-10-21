"""Regras semanticas locais, versionadas e rastreaveis ao RAW."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.models import IngestionJob, PncpAvaliacaoQualidade, RawPncpRecord

logger = logging.getLogger(__name__)
RULE_VERSION = "pncp-semantica-1"
# campo RAW, tipo, obrigatorio, destino; mesma regra para descoberta e atualizacao.
REGRAS = (
    ("numeroControlePNCP", "identidade", True, "external_id"),
    ("orgaoEntidade.cnpj", "cnpj", False, "orgao.cnpj"),
    ("unidadeOrgao.codigoIbge", "ibge", False, "unidade.codigoIbge"),
    ("objetoCompra", "texto", False, "objeto"),
    ("valorTotalEstimado", "dinheiro", False, "dados_semanticos.valor_total_estimado"),
    ("valorTotalHomologado", "dinheiro", False, "dados_semanticos.valor_total_homologado"),
    ("orcamentoSigiloso", "booleano", False, "dados_semanticos.orcamento_sigiloso"),
    ("orcamentoSigilosoCodigo", "sigilo", False, "dados_semanticos.orcamento_sigiloso_codigo"),
    ("orcamentoSigilosoDescricao", "texto", False, "dados_semanticos.orcamento_sigiloso_descricao"),
    ("dataAberturaProposta", "data", False, "dados_semanticos.data_abertura_proposta"),
    ("dataPublicacaoPncp", "data", False, "dados_semanticos.data_publicacao"),
    ("dataAtualizacao", "data", False, "dados_semanticos.data_atualizacao"),
    ("dataAtualizacaoGlobal", "data", False, "dados_semanticos.data_atualizacao_global"),
    ("tipoInstrumentoConvocatorioCodigo", "instrumento", False, "dados_semanticos.instrumento_codigo"),
    ("dataEncerramentoProposta", "data", False, "prazo_proposta_final"),
    ("modalidadeId", "modalidade", False, "dados_semanticos.modalidade_codigo"),
    ("situacaoCompraId", "situacao", False, "dados_semanticos.situacao_codigo"),
)
AUSENTE = object()


def obter(payload: dict, caminho: str) -> Any:
    valor = payload
    for parte in caminho.split("."):
        if not isinstance(valor, dict):
            return AUSENTE
        valor = valor.get(parte, AUSENTE)
    return valor


def converter_data(valor: str, *, fuso_origem: str | None = None) -> dict[str, Any]:
    if not isinstance(valor, str):
        raise ValueError("data deve ser string ISO")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        return {"valor": date.fromisoformat(valor).isoformat(), "tipo": "civil", "origem": valor}
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})?", valor):
        raise ValueError("data/hora ISO estendida obrigatoria")
    instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if instante.tzinfo is None and fuso_origem is not None:
        instante = instante.replace(tzinfo=ZoneInfo(fuso_origem))
        # A origem nao inclui offset, mas o manual define o fuso deste campo.
        # Horarios locais ambiguos ou inexistentes nao viram instantes arbitrarios.
        alternativa = instante.replace(fold=1)
        retorno = instante.astimezone(timezone.utc).astimezone(ZoneInfo(fuso_origem))
        if alternativa.utcoffset() != instante.utcoffset() or retorno.replace(tzinfo=None) != instante.replace(tzinfo=None):
            return {"valor": valor, "tipo": "sem_fuso", "origem": valor, "motivo": "horario_local_ambiguo_ou_inexistente"}
        return {"valor": instante.astimezone(timezone.utc).isoformat(), "tipo": "instante", "origem": valor,
                "offset_origem": None, "fuso_contrato": fuso_origem, "offset_aplicado": instante.strftime("%z")}
    if instante.tzinfo is None:
        # A origem nao informou fuso: nao presumir UTC nem horario de Brasilia.
        return {"valor": instante.isoformat(), "tipo": "sem_fuso", "origem": valor}
    return {"valor": instante.astimezone(timezone.utc).isoformat(), "tipo": "instante", "offset_origem": instante.strftime("%z"), "origem": valor}


def _converter(valor: Any, tipo: str, externo: str, *, campo: str = "") -> tuple[Any, str, str | None]:
    if tipo in {"texto", "identidade"}:
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("texto nao vazio obrigatorio")
        if tipo == "identidade" and valor.strip() != externo:
            raise ValueError("numeroControlePNCP divergente do RAW")
        return valor.strip(), "valid", None
    if tipo == "cnpj":
        if not isinstance(valor, str) or not re.fullmatch(r"[A-Za-z0-9]{12}[0-9]{2}", valor):
            raise ValueError("CNPJ deve ser string de 14 caracteres com DV numerico")
        valor = valor.upper()
        if len(set(valor)) == 1:
            raise ValueError("CNPJ invalido")
        # Receita Federal: modulo 11, valor de cada caractere ASCII menos 48.
        for limite, pesos in ((12, (5,4,3,2,9,8,7,6,5,4,3,2)), (13, (6,5,4,3,2,9,8,7,6,5,4,3,2))):
            resto = sum((ord(n) - 48) * peso for n, peso in zip(valor[:limite], pesos)) % 11
            if int(valor[limite]) != (0 if resto < 2 else 11 - resto):
                raise ValueError("CNPJ com digito verificador invalido")
        return valor, "valid", None
    if tipo == "ibge":
        if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{7}", valor):
            raise ValueError("IBGE deve ser string de 7 digitos, preservando zeros")
        return valor, "valid", None
    if tipo == "booleano":
        if type(valor) is not bool:
            raise ValueError("booleano obrigatorio")
        return valor, "valid", None
    if tipo == "dinheiro":
        if isinstance(valor, bool) or not isinstance(valor, (str, int, float, Decimal)):
            raise ValueError("valor monetario invalido")
        numero = Decimal(str(valor))
        if not numero.is_finite() or numero < 0:
            raise ValueError("valor monetario negativo ou nao finito")
        if numero.as_tuple().exponent < -4:
            return str(numero), "warning", "precisao_acima_contrato_preservada"
        if isinstance(valor, float):
            return str(numero), "warning", "origem_float_precisao_nao_comprovada"
        return str(numero), "valid", None
    if tipo == "data":
        dado = converter_data(valor, fuso_origem="America/Sao_Paulo" if campo in {"dataAberturaProposta", "dataEncerramentoProposta"} else None)
        return dado, "warning" if dado["tipo"] == "sem_fuso" else "valid", "fuso_ausente" if dado["tipo"] == "sem_fuso" else None
    # Dominios conhecidos nesta versao; novos codigos permanecem no destino e no RAW.
    dominio = {"modalidade": set(range(1, 14)), "situacao": set(range(1, 5)), "sigilo": {1, 2, 3}, "instrumento": {1, 2, 3}}[tipo]
    if type(valor) is not int and not (isinstance(valor, str) and valor.isascii() and valor.isdigit()):
        raise ValueError("codigo externo deve ser inteiro ou string de digitos")
    conhecido = int(valor) in dominio
    return valor, "valid" if conhecido else "warning", None if conhecido else "enum_desconhecido"


def avaliar_payload(raw: RawPncpRecord) -> dict[str, Any]:
    campos = {}
    payload = raw.payload
    if not isinstance(payload, dict):
        payload = {}
    for campo, tipo, obrigatorio, destino in REGRAS:
        valor = obter(payload, campo)
        estado = "absent" if valor is AUSENTE else "null" if valor is None else "empty" if isinstance(valor, str) and not valor.strip() else "present"
        evidencia = {"estado_origem": estado, "destino": destino, "tipo": tipo, "raw_record_id": raw.id, "valor": None, "motivo": None}
        if estado != "present":
            evidencia.update(status="error" if obrigatorio else "missing", motivo="campo_obrigatorio" if obrigatorio else "ausencia_legitima")
        else:
            try:
                normalizado, status, motivo = _converter(valor, tipo, raw.external_id, campo=campo)
                evidencia.update(valor=normalizado, status=status, motivo=motivo)
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                evidencia.update(status="error", motivo={
                    "identidade": "identidade_invalida_ou_divergente", "cnpj": "cnpj_tipo_formato_ou_verificador_invalido",
                    "ibge": "ibge_tipo_ou_formato_invalido", "dinheiro": "valor_monetario_invalido",
                    "data": "data_iso_invalida", "booleano": "booleano_invalido",
                    "instrumento": "codigo_instrumento_invalido", "sigilo": "codigo_sigilo_invalido", "texto": "texto_invalido", "modalidade": "codigo_modalidade_invalido", "situacao": "codigo_situacao_invalido",
                }[tipo])
        campos[campo] = evidencia
    instrumento = campos["tipoInstrumentoConvocatorioCodigo"]["valor"]
    if str(instrumento) in {"1", "2"}:
        for campo in ("dataAberturaProposta", "dataEncerramentoProposta"):
            if campos[campo]["status"] == "missing":
                campos[campo].update(status="error", motivo="prazo_obrigatorio_pelo_instrumento")
    # Pais com tipo incorreto nao pode parecer ausencia legitima de filho.
    for pai in ("orgaoEntidade", "unidadeOrgao"):
        if payload.get(pai) is not None and not isinstance(payload[pai], dict):
            filho = "orgaoEntidade.cnpj" if pai == "orgaoEntidade" else "unidadeOrgao.codigoIbge"
            campos[filho].update(status="error", motivo="entidade_deve_ser_objeto")
    estimado = campos["valorTotalEstimado"]
    if estimado["valor"] is not None and Decimal(estimado["valor"]) == 0:
        estimado.update(status="warning", motivo="zero_sigiloso" if (payload.get("orcamentoSigiloso") is True or str(payload.get("orcamentoSigilosoCodigo")) in {"2", "3"}) else "zero_sem_evidencia_gratuidade")
        # Zero desconhecido/sigiloso nao participa de agregacoes como valor gratuito.
        estimado["valor"] = None
    erros = any(c["status"] == "error" for c in campos.values())
    incompleto = any(c["status"] in {"warning", "missing"} for c in campos.values())
    return {"rule_version": RULE_VERSION, "data_quality_status": "invalid" if erros else "partial" if incompleto else "valid", "campos": campos}


def registrar_avaliacao_session(session: Session, raw: RawPncpRecord, avaliacao: dict) -> PncpAvaliacaoQualidade:
    # Resolver endpoint somente pela evidencia do job coletor, nunca presumir publicacao.
    endpoint = "unknown"
    match = re.fullmatch(r"job:(\d+):pagina:\d+", raw.run_id or "")
    if match:
        coletor = session.get(IngestionJob, int(match[1]))
        if coletor is not None:
            fluxo = coletor.scope_key.split(":", 1)[0]
            if fluxo in {"publicacao", "atualizacao"}:
                endpoint = "/v1/contratacoes/" + fluxo
    chave = {"raw_record_id": raw.id, "rule_version": RULE_VERSION}
    registro = session.get(PncpAvaliacaoQualidade, chave)
    if registro is None:
        registro = PncpAvaliacaoQualidade(**chave, endpoint=endpoint, run_id=raw.run_id, tentativas=0)
        session.add(registro)
    registro.decisao = "ISOLADO" if avaliacao["data_quality_status"] == "invalid" else "AVALIADO"
    registro.campos = avaliacao["campos"]
    registro.data_quality_status = avaliacao["data_quality_status"]
    registro.tentativas += 1
    registro.avaliado_em = datetime.now(timezone.utc)
    for campo, evidencia in avaliacao["campos"].items():
        if evidencia["status"] in {"warning", "error"}:
            logger.warning("Qualidade semantica PNCP", extra={"raw_record_id": raw.id, "campo": campo, "motivo": evidencia["motivo"], "rule_version": RULE_VERSION})

    return registro


def consultar_qualidade_session(session: Session, *, run_id: str | None = None, endpoint: str | None = None) -> dict:
    consulta = select(PncpAvaliacaoQualidade)
    if run_id is not None:
        consulta = consulta.where(PncpAvaliacaoQualidade.run_id == run_id)
    if endpoint is not None:
        consulta = consulta.where(PncpAvaliacaoQualidade.endpoint == endpoint)
    avaliacoes = session.scalars(consulta).all()
    metricas, excecoes = {}, []
    for avaliacao in avaliacoes:
        if avaliacao.decisao in {"ANTIGO", "AMBIGUO", "INCOMPLETO"}:
            excecoes.append({"raw_id": avaliacao.raw_record_id, "campo": "versao", "motivo": avaliacao.decisao, "impacto": "versao_atual_preservada", "decisao": "nao_substituir", "tentativas": avaliacao.tentativas, "evidencia_reprocessamento": avaliacao.tentativas > 1, "rule_version": avaliacao.rule_version})
        for campo, evidencia in avaliacao.campos.items():
            chave = (avaliacao.run_id, avaliacao.endpoint, avaliacao.rule_version, campo)
            metrica = metricas.setdefault(chave, {"run_id": avaliacao.run_id, "endpoint": avaliacao.endpoint, "rule_version": avaliacao.rule_version, "campo": campo, "total": 0, "presentes": 0, "validos": 0, "quarentena": 0})
            metrica["total"] += 1
            metrica["presentes"] += evidencia["estado_origem"] == "present"
            metrica["validos"] += evidencia["status"] == "valid"
            metrica["quarentena"] += avaliacao.data_quality_status == "invalid"
            if evidencia["status"] != "valid":
                excecoes.append({"raw_id": avaliacao.raw_record_id, "campo": campo, "motivo": evidencia["motivo"], "impacto": evidencia["status"], "decisao": "isolar" if avaliacao.data_quality_status == "invalid" else "preservar_com_ressalva", "tentativas": avaliacao.tentativas, "evidencia_reprocessamento": avaliacao.tentativas > 1, "rule_version": avaliacao.rule_version})
    for metrica in metricas.values():
        metrica["completude"] = metrica["presentes"] / metrica["total"]
        metrica["validade"] = metrica["validos"] / metrica["total"]
    cobertura = session.execute(text("""
        WITH base AS (
          SELECT r.*, a.data_quality_status,
            coalesce(a.endpoint, CASE WHEN split_part(j.scope_key, ':', 1) IN ('publicacao', 'atualizacao')
              THEN '/v1/contratacoes/' || split_part(j.scope_key, ':', 1) ELSE 'unknown' END) AS endpoint,
            a.raw_record_id AS avaliado_id
          FROM raw_pncp_records r
          LEFT JOIN pncp_avaliacoes_qualidade a ON a.raw_record_id = r.id AND a.rule_version = :regra
          LEFT JOIN ingestion_jobs j ON j.id = CASE WHEN r.run_id ~ '^job:[0-9]+:pagina:[0-9]+$'
              THEN split_part(r.run_id, ':', 2)::bigint ELSE NULL END
          WHERE r.source = 'PNCP' AND r.entity_type = 'CONTRATACAO'
        )
        SELECT b.run_id, b.endpoint, count(*) AS raw_total,
          count(b.avaliado_id) AS avaliados,
          count(b.avaliado_id)::numeric / nullif(count(*), 0) AS cobertura,
          count(DISTINCT (b.source, b.entity_type, b.external_id)) AS entidades_distintas,
          count(DISTINCT (b.source, b.entity_type, b.external_id, b.payload_hash)) AS versoes_distintas,
          count(*) - count(DISTINCT (b.source, b.entity_type, b.external_id, b.payload_hash)) AS duplicatas_raw,
          count(*) FILTER (WHERE b.data_quality_status = 'invalid') AS invalidos,
          count(*) FILTER (WHERE q.raw_record_id IS NOT NULL) AS quarentena_atual,
          count(*) FILTER (WHERE o.raw_record_id = b.id) AS versoes_canonicas,
          count(*) FILTER (WHERE b.data_quality_status IN ('valid','partial') AND o.raw_record_id IS NULL) AS sem_entidade_normalizada
        FROM base b
        LEFT JOIN pncp_oportunidades o ON o.source = b.source AND o.external_id = b.external_id
        LEFT JOIN pncp_quarentena q ON q.raw_record_id = b.id
        WHERE (CAST(:run_id AS text) IS NULL OR b.run_id = :run_id)
          AND (CAST(:endpoint AS text) IS NULL OR b.endpoint = :endpoint)
        GROUP BY b.run_id, b.endpoint
        ORDER BY b.run_id, b.endpoint
    """), {"regra": RULE_VERSION, "run_id": run_id, "endpoint": endpoint}).mappings().all()
    return {"rule_version": RULE_VERSION, "metricas": list(metricas.values()), "cobertura_integridade_unicidade": [dict(r) for r in cobertura], "excecoes": excecoes, "reprocessamentos": [
        {"raw_id": a.raw_record_id, "rule_version": a.rule_version, "tentativas": a.tentativas,
         "decisao": a.decisao, "avaliado_em": a.avaliado_em,
         "sucesso": a.decisao in {"NOVO", "ALTERADO", "INALTERADO"}}
        for a in avaliacoes if a.tentativas > 1
    ]}


def estado_sincronizacao(*, qualidade: str | None, executando: bool = False, indisponivel: bool = False, desatualizada: bool = False) -> str:
    if qualidade == "invalid": return "quarantined"
    if indisponivel: return "source_unavailable"
    if executando: return "syncing"
    if qualidade in (None, "not_evaluated"): return "pending"
    if desatualizada: return "stale"
    return "synchronized" if qualidade == "valid" else "partially_synchronized"
