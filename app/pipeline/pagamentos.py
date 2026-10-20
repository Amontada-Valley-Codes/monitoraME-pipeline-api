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
from app.utils import normalizar_cnpj, normalizar_texto, validar_cnpj

CAMPOS_CHAVE = (
    "codigo_municipio", "exercicio_orcamento", "codigo_orgao",
    "codigo_unidade_orcamentaria", "data_emissao_empenho", "numero_empenho",
)
CATEGORIAS = ("ME", "EPP", "DEMAIS", "SEM_CLASSIFICACAO", "NAO_CNPJ")


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
    for orgao in orgaos:
        codigo = _texto(orgao, "codigo_orgao")
        if codigo in cadastro:
            raise ValueError(f"Orgao duplicado: {codigo}.")
        cadastro[codigo] = orgao
        nome = normalizar_texto(_texto(orgao, "nome_orgao"))
        tipo = _texto(orgao, "codigo_tipo_unidade")
        if tipo == "02" or "CAMARA" in nome:
            camaras.add(codigo)
    if not camaras:
        raise ValueError("Cadastro nao permite identificar a Camara; revisar orgaos.")

    indice = {}
    for empenho in empenhos:
        chave = _chave(empenho)
        if chave[:2] != (codigo_municipio, str(exercicio * 100)):
            raise ValueError(f"Empenho fora do recorte: {chave}.")
        if chave in indice:
            raise ValueError(f"Chave de empenho duplicada: {chave}.")
        indice[chave] = empenho

    vistos = set()
    elegiveis = []
    excluidos = defaultdict(int)
    for pagamento in pagamentos:
        chave = _chave(pagamento)
        chave_pagamento = (*chave, _texto(pagamento, "numero_sub_empenho"),
                           _texto(pagamento, "numero_nota_pagamento"))
        if chave_pagamento in vistos:
            raise ValueError(f"Chave de pagamento duplicada: {chave_pagamento}.")
        vistos.add(chave_pagamento)
        if chave not in indice:
            raise ValueError(f"Pagamento sem empenho correspondente: {chave_pagamento}.")
        if chave[2] not in cadastro:
            raise ValueError(f"Orgao ausente do cadastro: {chave[2]}.")
        estado = _texto(pagamento, "estado_de_estornado")
        if estado not in {"0", "1"}:
            raise ValueError(f"Estado de estorno desconhecido: {estado}.")
        valor = valor_para_centavos(pagamento.get("valor_nota_pagamento"), "valor_nota_pagamento")
        empenho = indice[chave]
        natureza = _texto(empenho, "codigo_elemento_despesa")
        if not re.fullmatch(r"[0-9]{8}", natureza):
            raise ValueError(f"Codigo de natureza invalido: {natureza}.")
        elemento = natureza[4:6]
        motivo = ("camara" if chave[2] in camaras else "estornado" if estado == "1"
                  else "fora_naturezas" if elemento not in NATUREZAS_DESPESA_MONITORADAS else None)
        if motivo:
            excluidos[motivo] += 1
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
        "orgaos_camara": sorted(camaras), "chaves_duplicadas": 0,
        "pagamentos_sem_empenho": 0,
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
                "valor_pago_centavos": 0, "quantidade_pagamentos": 0,
            })
            grupo["valor_pago_centavos"] += valor
            grupo["quantidade_pagamentos"] += 1
    total = sum(totais.values())
    assert sum(categorias.values()) == total
    return {
        "total_me_centavos": categorias["ME"], "me_por_elemento_centavos": me_por_elemento,
        "fornecedores_me": sorted(grupos.values(), key=lambda g: (g["elemento"], g["cnpj"])),
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
        "auditoria": auditoria, **resumir_pagamentos(elegiveis, classificacoes),
    }
