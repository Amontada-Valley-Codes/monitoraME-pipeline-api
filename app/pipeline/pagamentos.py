"""Pagamentos da Prefeitura por exercicio orcamentario e porte cadastral.

A conciliacao financeira precede o enriquecimento; nenhuma falha cadastral
remove valores do controle financeiro. Valores publicos de fornecedores: ME.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import re

from app.core.config import NATUREZAS_DESPESA_MONITORADAS
from app.pipeline.cleaners.opencnpj import normalizar_booleano, normalizar_porte_empresarial
from app.pipeline.cleaners.tce_despesas import valor_para_centavos
from app.pipeline.persistence.tce_orgaos import classificar_orgao
from app.pipeline.referencias import referencias_entre
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
    """Compatibilidade para consumidores do helper privado anterior."""
    return referencias_entre(inicio, fim)


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
        tipo = str(orgao.get("codigo_tipo_unidade") or "").strip() or None
        eh_camara = classificacao["classificacao"] == "CAMARA"
        classificacoes_orgaos.append({
            "codigo_orgao": codigo,
            "nome_orgao": orgao["nome_orgao"],
            "codigo_tipo_unidade": tipo,
            "classificacao": "CAMARA" if eh_camara else "EXECUTIVO_MUNICIPAL",
            "regra": classificacao["regra"],
            "confianca": classificacao["confianca"],
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
        cnpj = _cnpj(empenho)
        motivo = ("camara" if chave[2] in camaras else "estado_estorno_desconhecido" if estado not in {"0", "1"}
                  else "estornado" if estado == "1"
                  else "fora_naturezas" if elemento not in NATUREZAS_DESPESA_MONITORADAS
                  else "fornecedor_nao_cnpj" if cnpj is None else None)
        if motivo:
            excluidos[motivo] += 1
        if motivo == "estado_estorno_desconhecido":
            anomalias.append({
                "codigo": "ESTADO_ESTORNO_DESCONHECIDO",
                "chave_pagamento": list(chave_pagamento),
                "estado_de_estornado": estado or None,
                "valor_centavos": valor,
            })
        if motivo:
            continue
        elegiveis.append({
            "chave_pagamento": list(chave_pagamento), "elemento": elemento,
            "cnpj": cnpj,
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


def _classificar_cnpj(cnpj: str, cache: dict | None) -> dict:
    """Converte um snapshot cadastral local em classificacao analitica."""
    dados = dict(cache.get("dados_normalizados") or {}) if cache else {}
    status = str(cache.get("opencnpj_status") or "nao_consultado") if cache else "nao_consultado"
    porte = normalizar_porte_empresarial(
        dados.get("porte_padronizado") or dados.get("opencnpj_porte_empresa")
    )
    if status == "ok" and porte not in {"ME", "EPP", "DEMAIS"}:
        status = "porte_desconhecido"
    indicador_mei = (
        dados["optante_mei"]
        if "optante_mei" in dados
        else dados.get("opencnpj_opcao_mei")
    )
    indicador_mei_normalizado = normalizar_booleano(indicador_mei)
    indicador_mei = (
        indicador_mei_normalizado
        if isinstance(indicador_mei_normalizado, bool)
        else None
    )
    return {
        "cnpj_completo": cnpj, "cnpj_basico": cnpj[:8],
        "porte_original": dados.get("opencnpj_porte_empresa") or dados.get("porte_padronizado"),
        "porte_codigo": {"ME": "01", "EPP": "03", "DEMAIS": "05"}.get(porte),
        "indicador_mei": indicador_mei,
        "fonte_cadastral": "RECEITA_FEDERAL_VIA_OPENCNPJ",
        "referencia_base_cadastral": (
            cache.get("observado_em").isoformat()
            if cache and hasattr(cache.get("observado_em"), "isoformat")
            else cache.get("observado_em") if cache else None
        ),
        "consultado_em": None,
        "situacao_classificacao": status,
    }


def classificar_fornecedores(
    pagamentos: list[dict],
    caches: dict[str, dict],
) -> dict[str, dict]:
    """Classifica fornecedores a partir de snapshots recebidos pelo chamador."""
    cnpjs = sorted({p["cnpj"] for p in pagamentos if p["cnpj"]})
    return {cnpj: _classificar_cnpj(cnpj, caches.get(cnpj)) for cnpj in cnpjs}


def resumir_pagamentos(pagamentos: list[dict], classificacoes: dict[str, dict]) -> dict:
    totais = {codigo: 0 for codigo in NATUREZAS_DESPESA_MONITORADAS}
    categorias = {categoria: 0 for categoria in CATEGORIAS}
    me_por_elemento = dict(totais)
    mpe_por_elemento = dict(totais)
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
        if categoria in {"ME", "EPP"} or cadastro.get("indicador_mei") is True:
            mpe_por_elemento[elemento] += valor
    total = sum(totais.values())
    assert sum(categorias.values()) == total
    fornecedores_me = []
    for grupo in grupos.values():
        nomes = grupo.pop("_nomes_observados")
        grupo["nomes_observados"] = [nomes[chave] for chave in sorted(nomes)]
        fornecedores_me.append(grupo)
    return {
        "total_me_centavos": categorias["ME"], "me_por_elemento_centavos": me_por_elemento,
        "total_mpe_centavos": None if pendencias else sum(mpe_por_elemento.values()),
        "total_mpe_identificado_centavos": sum(mpe_por_elemento.values()),
        "mpe_por_elemento_centavos": mpe_por_elemento,
        "fornecedores_me": sorted(fornecedores_me, key=lambda g: (g["elemento"], g["cnpj"])),
        "controle_financeiro": {
            "total_centavos": total, "por_elemento_centavos": totais,
            "por_categoria_centavos": categorias, "reconciliado": True,
        },
        "classificacao_completa": not pendencias,
        "pendencias": pendencias, "classificacoes": list(classificacoes.values()),
    }


def consultar_pagamentos_prefeitura(
    *, codigo_municipio: str, exercicio: int, referencia_final: str,
    coleta: dict, consultar_porte: bool = True,
    fornecedores_cache: dict[str, dict] | None = None,
) -> dict:
    """Transforma uma coleta fornecida sem acessar fontes externas ou persistir dados."""
    if (coleta["codigo_municipio"], coleta["exercicio"], coleta["referencia_final"]) != (
        codigo_municipio.strip(), exercicio, referencia_final,
    ):
        raise ValueError("Coleta fornecida pertence a outro recorte.")
    elegiveis, auditoria = conciliar_pagamentos(
        coleta["empenhos"], coleta["pagamentos"], coleta["orgaos"],
        codigo_municipio=codigo_municipio.strip(), exercicio=exercicio,
    )
    if consultar_porte and fornecedores_cache is None:
        raise ValueError(
            "fornecedores_cache e obrigatorio quando consultar_porte=True; "
            "a consulta analitica nao executa ingestao nem leitura implicita."
        )
    classificacoes = (
        classificar_fornecedores(elegiveis, fornecedores_cache or {})
        if consultar_porte
        else {}
    )
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
        conferencia_portal["bate"] = not any(divergencias.values())
    return {
        "fonte": "TCE-CE/SIM", "medida": "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA",
        "unidade_monetaria": "CENTAVOS", "administracao": "PREFEITURA",
        "codigo_municipio": codigo_municipio.strip(), "exercicio_orcamento": exercicio * 100,
        "referencias_empenhos": [f"{exercicio}-01", f"{exercicio}-12"],
        "referencias_pagamentos": [f"{exercicio}-01", referencia_final],
        "coleta_iniciada_em": coleta["coleta_iniciada_em"],
        "coleta_concluida_em": coleta["coleta_concluida_em"],
        "criterio_me": {
            "porte_codigo": "01",
            "rotulo": "ME",
            "observacao": "Subtotal ME; nao deve ser rotulado como MPE.",
        },
        "criterio_mpe": {
            "regra": "ME + EPP + MEI quando indicado pelo cadastro",
        },
        "referencia_base_cadastral": None, "porte_historico_comprovado": False,
        "consulta_cadastral_executada": consultar_porte,
        "indicador_me_validado": (
            None if conferencia_portal is None
            else bool(conferencia_portal["bate"] and resumo["classificacao_completa"])
        ),
        "particionamento_pagamentos": coleta.get("particionamento_pagamentos"),
        "versao_regra_financeira": "tce-pagamentos-prefeitura.v2",
        "fonte_referencia_financeira": "TCE-CE SIM e Portal da Transparencia",
        "conferencia_portal": conferencia_portal,
        "auditoria": auditoria, **resumo,
    }
