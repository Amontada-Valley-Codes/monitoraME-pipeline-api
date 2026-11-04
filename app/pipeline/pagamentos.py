"""Pagamentos da Prefeitura por exercicio orcamentario e porte cadastral.

A conciliacao financeira precede o enriquecimento; nenhuma falha cadastral
remove valores do controle financeiro. Valores publicos de fornecedores: ME.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import re
from typing import Any

from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.pipeline.cleaners.opencnpj import normalizar_porte_empresarial
from app.pipeline.cleaners.tce_despesas import valor_para_centavos
from app.pipeline.ingestion import fornecedores, tce
from app.pipeline.persistence.tce_orgaos import classificar_orgao
from app.utils import normalizar_cnpj, normalizar_texto, validar_cnpj

CAMPOS_CHAVE = (
    "codigo_municipio", "exercicio_orcamento", "codigo_orgao",
    "codigo_unidade_orcamentaria", "data_emissao_empenho", "numero_empenho",
)
CATEGORIAS = ("ME", "EPP", "DEMAIS", "SEM_CLASSIFICACAO", "NAO_CNPJ")
REFERENCIA_PORTAL_ACARAU_2025 = {
    "39": 6_631_844_119,
    "30": 2_953_129_365,
    "51": 3_132_409_295,
    "52": 516_692_530,
    "32": 187_701_200,
    "40": 1_048_364,
    "35": 108_328_071,
}


def _texto(registro: dict, campo: str) -> str:
    valor = registro.get(campo)
    if valor is None or isinstance(valor, bool) or not str(valor).strip():
        raise ValueError(f"Campo obrigatorio invalido: {campo}.")
    return str(valor).strip()


def _data(valor: str) -> str:
    if len(valor) == 8 and valor.isdigit():
        valor = f"{valor[:4]}-{valor[4:6]}-{valor[6:]}"
    return date.fromisoformat(valor[:10]).isoformat()


def _chave(registro: dict) -> tuple[str, ...]:
    valores = [_texto(registro, campo) for campo in CAMPOS_CHAVE]
    valores[4] = _data(valores[4])
    return tuple(valores)


def _referencias(inicio: str, fim: str) -> list[str]:
    def indice(valor: str) -> int:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", valor):
            raise ValueError("Referencia deve usar YYYY-MM.")
        data = date.fromisoformat(valor + "-01")
        return data.year * 12 + data.month - 1
    primeiro, ultimo = indice(inicio), indice(fim)
    if primeiro > ultimo:
        raise ValueError("Referencia inicial posterior a final.")
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(primeiro, ultimo + 1)]


def _cnpj(empenho: dict) -> str | None:
    original = str(empenho.get("numero_documento_negociante") or "").strip()
    if str(empenho.get("codigo_tipo_negociante")).strip() != "1":
        return None
    if not re.fullmatch(r"[0-9./\s-]+", original):
        return None
    documento = normalizar_cnpj(original)
    return documento if validar_cnpj(documento) else None


def conciliar_pagamentos(
    empenhos: list[dict], pagamentos: list[dict], orgaos: list[dict], *,
    codigo_municipio: str, exercicio: int,
) -> tuple[list[dict], dict]:
    """Rejeita chaves repetidas e cruzamentos incompletos antes de calcular."""
    cadastro: dict[str, dict] = {}
    camaras = set()
    executivo_municipal = set()
    classificacoes_orgaos = []
    for orgao in orgaos:
        classificacao = classificar_orgao(orgao)
        codigo = str(classificacao["codigo_orgao"])
        if codigo in cadastro:
            raise ValueError(f"Orgao duplicado: {codigo}.")
        cadastro[codigo] = orgao
        nome = normalizar_texto(_texto(orgao, "nome_orgao"))
        tipo = str(orgao.get("codigo_tipo_unidade") or "").strip() or None
        # codigo_tipo_unidade varia entre cadastros/anos. O nome e os dados
        # vinculados identificam o Legislativo; o codigo do tipo e registrado
        # como evidência, nunca tratado como regra universal.
        eh_camara = classificacao["classificacao"] == "CAMARA"
        classificacoes_orgaos.append({
            "codigo_orgao": codigo,
            "nome_orgao": orgao["nome_orgao"],
            "codigo_tipo_unidade": tipo,
            "classificacao": "CAMARA" if eh_camara else "EXECUTIVO_MUNICIPAL",
            "regra": "NOME_ORGAO_NORMALIZADO",
            "confianca": "ALTA" if eh_camara or any(
                token in nome for token in ("PREFEITURA", "SECRETARIA", "GABINETE", "AUTARQUIA")
            ) else "REVISAR",
        })
        if eh_camara:
            camaras.add(codigo)
        elif classificacao["confianca"] == "ALTA":
            executivo_municipal.add(codigo)
    if not camaras:
        raise ValueError("Cadastro nao permite identificar a Camara; revisar orgaos.")
    if not executivo_municipal:
        raise ValueError("Cadastro nao permite identificar orgaos do Executivo municipal; revisar orgaos.")

    indice = {}
    for empenho in empenhos:
        chave = _chave(empenho)
        if chave[:2] != (codigo_municipio, str(exercicio * 100)):
            raise ValueError(f"Empenho fora do recorte: {chave}.")
        if chave in indice:
            raise ValueError(f"Chave de empenho duplicada: {chave}.")
        indice[chave] = empenho

    vistos: dict[tuple[str, ...], dict] = {}
    elegiveis = []
    excluidos = defaultdict(int)
    anomalias = []
    for pagamento in pagamentos:
        chave = _chave(pagamento)
        chave_pagamento = (*chave, _texto(pagamento, "numero_sub_empenho"),
                           _texto(pagamento, "numero_nota_pagamento"))
        pagamento_anterior = vistos.get(chave_pagamento)
        if pagamento_anterior is not None:
            if pagamento_anterior != pagamento:
                raise ValueError(f"Duplicidade conflitante de pagamento: {chave_pagamento}.")
            excluidos["duplicado_identico"] += 1
            anomalias.append({
                "codigo": "PAGAMENTO_DUPLICADO_IDENTICO",
                "chave_pagamento": list(chave_pagamento),
                "referencia_documentacao": pagamento.get("data_referencia_doc"),
            })
            continue
        vistos[chave_pagamento] = pagamento
        if chave not in indice:
            raise ValueError(f"Pagamento sem empenho correspondente: {chave_pagamento}.")
        if chave[2] not in cadastro:
            raise ValueError(f"Orgao ausente do cadastro: {chave[2]}.")
        estado_informado = pagamento.get("estado_de_estornado")
        estado = "" if estado_informado is None else str(estado_informado).strip()
        valor = valor_para_centavos(pagamento.get("valor_nota_pagamento"), "valor_nota_pagamento")
        empenho = indice[chave]
        natureza = _texto(empenho, "codigo_elemento_despesa")
        if not re.fullmatch(r"[0-9]{8}", natureza):
            raise ValueError(f"Codigo de natureza invalido: {natureza}.")
        elemento = natureza[4:6]
        motivo = ("camara" if chave[2] in camaras else "estado_estorno_desconhecido" if estado not in {"0", "1"}
                  else "estornado" if estado == "1"
                  else "fora_naturezas" if elemento not in NATUREZAS_DESPESA_MONITORADAS else None)
        if motivo:
            excluidos[motivo] += 1
            if motivo == "estado_estorno_desconhecido":
                anomalias.append({
                    "codigo": "ESTADO_ESTORNO_DESCONHECIDO",
                    "chave_pagamento": list(chave_pagamento),
                    "estado_de_estornado": estado or None,
                    "valor_centavos": valor,
                })
            continue
        elegiveis.append({
            "chave_pagamento": list(chave_pagamento), "elemento": elemento,
            "cnpj": _cnpj(empenho),
            "documento_original": empenho.get("numero_documento_negociante"),
            "tipo_documento": empenho.get("codigo_tipo_negociante"),
            "nome": empenho.get("nome_negociante"),
            "data_pagamento": _data(_texto(pagamento, "data_nota_pagamento")),
            "referencia_documentacao": _texto(pagamento, "data_referencia_doc"),
            "valor_centavos": valor,
        })
    return elegiveis, {
        "empenhos_coletados": len(empenhos), "pagamentos_coletados": len(pagamentos),
        "pagamentos_elegiveis": len(elegiveis), "excluidos": dict(excluidos),
        "orgaos_executivo_municipal": sorted(executivo_municipal),
        "orgaos_camara": sorted(camaras), "chaves_duplicadas": 0,
        "pagamentos_sem_empenho": 0,
        "classificacoes_orgaos": classificacoes_orgaos,
        "anomalias": anomalias,
    }


def _classificar_cnpj(cnpj: str) -> dict:
    instante = datetime.now(timezone.utc).isoformat()
    dados = fornecedores.coletar_fornecedor(cnpj)
    bruto = dados.get("opencnpj") or {}
    status = dados.get("opencnpj_status", "indisponivel")
    documento_retornado = normalizar_cnpj(bruto.get("cnpj"))
    porte = None
    if status == "ok" and documento_retornado == cnpj:
        porte = normalizar_porte_empresarial(dados.get("porte"))
        if porte not in {"ME", "EPP", "DEMAIS"}:
            status = "porte_desconhecido"
    elif status == "ok":
        status = "cnpj_divergente"
    return {
        "cnpj_completo": cnpj, "cnpj_basico": cnpj[:8],
        "porte_original": dados.get("porte"),
        "porte_codigo": {"ME": "01", "EPP": "03", "DEMAIS": "05"}.get(porte),
        "indicador_mei": bruto.get("opcao_mei"),
        "fonte_cadastral": "RECEITA_FEDERAL_VIA_OPENCNPJ",
        "referencia_base_cadastral": None, "consultado_em": instante,
        "situacao_classificacao": status,
    }


def classificar_fornecedores(pagamentos: list[dict]) -> dict[str, dict]:
    """Consulta cada CNPJ uma vez, com no maximo quatro requisicoes simultaneas."""
    cnpjs = sorted({p["cnpj"] for p in pagamentos if p["cnpj"]})
    with ThreadPoolExecutor(max_workers=4) as executor:
        return dict(zip(cnpjs, executor.map(_classificar_cnpj, cnpjs)))


def resumir_pagamentos(pagamentos: list[dict], classificacoes: dict[str, dict]) -> dict:
    totais = {codigo: 0 for codigo in NATUREZAS_DESPESA_MONITORADAS}
    categorias = {categoria: 0 for categoria in CATEGORIAS}
    me_por_elemento = dict(totais)
    grupos: dict[tuple, dict] = {}
    pendencias = []
    for pagamento in pagamentos:
        cnpj, elemento, valor = pagamento["cnpj"], pagamento["elemento"], pagamento["valor_centavos"]
        cadastro = classificacoes.get(cnpj, {})
        porte = cadastro.get("porte_codigo") if cadastro.get("situacao_classificacao") == "ok" else None
        categoria = "NAO_CNPJ" if not cnpj else {"01": "ME", "03": "EPP", "05": "DEMAIS"}.get(porte, "SEM_CLASSIFICACAO")
        totais[elemento] += valor
        categorias[categoria] += valor
        if categoria in {"SEM_CLASSIFICACAO", "NAO_CNPJ"}:
            pendencias.append({
                "chave_pagamento": pagamento["chave_pagamento"], "cnpj": cnpj,
                "documento_original": pagamento["documento_original"],
                "tipo_documento": pagamento["tipo_documento"], "valor_centavos": valor,
                "motivo": cadastro.get("situacao_classificacao", "nao_consultado" if cnpj else "documento_nao_cnpj_valido"),
            })
        if categoria == "ME":
            me_por_elemento[elemento] += valor
            grupo = grupos.setdefault((elemento, cnpj), {
                "elemento": elemento, "cnpj": cnpj, "nome": pagamento["nome"],
                "_nomes_observados": {},
                "valor_pago_centavos": 0, "quantidade_pagamentos": 0,
            })
            nome = str(pagamento.get("nome") or "").strip()
            nome_chave = normalizar_texto(nome)
            if nome_chave:
                grupo["_nomes_observados"].setdefault(nome_chave, nome)
            grupo["valor_pago_centavos"] += valor
            grupo["quantidade_pagamentos"] += 1
    total = sum(totais.values())
    assert sum(categorias.values()) == total
    fornecedores_me = []
    for grupo in grupos.values():
        nomes = grupo.pop("_nomes_observados")
        grupo["nomes_observados"] = [nomes[chave] for chave in sorted(nomes)]
        fornecedores_me.append(grupo)
    return {
        "total_me_centavos": categorias["ME"], "me_por_elemento_centavos": me_por_elemento,
        "fornecedores_me": sorted(fornecedores_me, key=lambda g: (g["elemento"], g["cnpj"])),
        "controle_financeiro": {
            "total_centavos": total, "por_elemento_centavos": totais,
            "por_categoria_centavos": categorias, "reconciliado": True,
        },
        "classificacao_completa": not pendencias,
        "pendencias": pendencias, "classificacoes": list(classificacoes.values()),
    }


def coletar_documentos_prefeitura(
    *, codigo_municipio: str, exercicio: int, referencia_final: str,
) -> dict:
    codigo_municipio = codigo_municipio.strip()
    if not re.fullmatch(r"[0-9]{3}", codigo_municipio):
        raise ValueError("Codigo do municipio deve conter tres digitos.")
    referencias = _referencias(f"{exercicio:04d}-01", referencia_final)
    if referencia_final < f"{exercicio:04d}-12":
        raise ValueError("Referencia final deve cobrir pelo menos dezembro do exercicio.")
    inicio = datetime.now(timezone.utc).isoformat()
    orgaos = tce.buscar_documentos_exercicio(
        "orgaos", codigo_municipio=codigo_municipio, exercicio=exercicio,
    )
    empenhos, pagamentos = [], []
    for referencia in referencias:
        params = dict(codigo_municipio=codigo_municipio, exercicio=exercicio, competencia=referencia)
        if referencia[:4] == str(exercicio):
            empenhos.extend(tce.buscar_documentos_exercicio("notas_empenhos", **params))
        pagamentos.extend(tce.buscar_documentos_exercicio("notas_pagamentos", **params))
    return {
        "codigo_municipio": codigo_municipio, "exercicio": exercicio,
        "referencia_final": referencia_final, "coleta_iniciada_em": inicio,
        "coleta_concluida_em": datetime.now(timezone.utc).isoformat(),
        "orgaos": orgaos, "empenhos": empenhos, "pagamentos": pagamentos,
    }


def consultar_pagamentos_prefeitura(
    *, codigo_municipio: str, exercicio: int, referencia_final: str,
    consultar_porte: bool = True, coleta: dict | None = None,
) -> dict:
    if coleta is None:
        coleta = coletar_documentos_prefeitura(
            codigo_municipio=codigo_municipio, exercicio=exercicio, referencia_final=referencia_final,
        )
    if (coleta["codigo_municipio"], coleta["exercicio"], coleta["referencia_final"]) != (
        codigo_municipio.strip(), exercicio, referencia_final,
    ):
        raise ValueError("Coleta fornecida pertence a outro recorte.")
    elegiveis, auditoria = conciliar_pagamentos(
        coleta["empenhos"], coleta["pagamentos"], coleta["orgaos"],
        codigo_municipio=codigo_municipio.strip(), exercicio=exercicio,
    )
    classificacoes = classificar_fornecedores(elegiveis) if consultar_porte else {}
    resumo = resumir_pagamentos(elegiveis, classificacoes)
    conferencia_portal = None
    if codigo_municipio.strip() == "004" and exercicio == 2025 and referencia_final == "2026-09":
        valores = resumo["controle_financeiro"]["por_elemento_centavos"]
        divergencias = {
            elemento: valores.get(elemento, 0) - esperado
            for elemento, esperado in REFERENCIA_PORTAL_ACARAU_2025.items()
        }
        conferencia_portal = {
            "referencia": "TCE-CE Portal da Transparencia, aba Prefeitura, conferida em 2026-10-19",
            "recorte_referencias_pagamentos": ["2025-01", "2026-09"],
            "valores_esperados_centavos": REFERENCIA_PORTAL_ACARAU_2025,
            "divergencia_centavos_por_elemento": divergencias,
            "total_esperado_centavos": sum(REFERENCIA_PORTAL_ACARAU_2025.values()),
            "total_apurado_centavos": resumo["controle_financeiro"]["total_centavos"],
            "divergencia_total_centavos": resumo["controle_financeiro"]["total_centavos"] - sum(REFERENCIA_PORTAL_ACARAU_2025.values()),
        }
    return {
        "fonte": "TCE-CE/SIM", "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "unidade_monetaria": "CENTAVOS", "administracao": "PREFEITURA",
        "codigo_municipio": codigo_municipio.strip(), "exercicio_orcamento": exercicio * 100,
        "referencias_empenhos": [f"{exercicio}-01", f"{exercicio}-12"],
        "referencias_pagamentos": [f"{exercicio}-01", referencia_final],
        "coleta_iniciada_em": coleta["coleta_iniciada_em"],
        "coleta_concluida_em": coleta["coleta_concluida_em"],
        "criterio_me": {"porte_codigo": "01", "excluir_mei": False},
        "referencia_base_cadastral": None, "porte_historico_comprovado": False,
        "consulta_cadastral_executada": consultar_porte,
        "versao_regra_financeira": "tce-pagamentos-prefeitura.v1",
        "fonte_referencia_financeira": "TCE-CE SIM e Portal da Transparencia",
        "conferencia_portal": conferencia_portal,
        "auditoria": auditoria, **resumo,
    }
