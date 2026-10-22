"""Contratos canônicos dos órgãos e pagamentos do SIM/TCE-CE."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.pipeline.cleaners.tce_despesas import (
    FONTE_TCE_CE,
    ContratoDespesaTceInvalido,
    _chave_empenho,
    _competencia,
    _data_civil,
    _exercicio,
    _inteiro,
    _texto,
    chave_pagamento,
    valor_para_centavos,
)


def _sem_acentos(valor: str) -> str:
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor)
        if not unicodedata.combining(caractere)
    )


def classificar_administracao(nome_orgao: str) -> str:
    """Separa Câmara do conjunto da Prefeitura usando o cadastro oficial do órgão."""
    nome = re.sub(r"\s+", " ", _sem_acentos(nome_orgao).upper()).strip()
    if re.search(r"\bCAMARA\b", nome):
        return "CAMARA"
    return "PREFEITURA"


def mapear_orgao(registro: dict[str, Any]) -> dict[str, Any]:
    codigo_municipio = _texto(registro.get("codigo_municipio"), "codigo_municipio")
    codigo_orgao = _texto(registro.get("codigo_orgao"), "codigo_orgao")
    nome_orgao = _texto(registro.get("nome_orgao"), "nome_orgao")
    tipo_unidade = _texto(
        registro.get("codigo_tipo_unidade"), "codigo_tipo_unidade", obrigatorio=False
    )
    assert codigo_municipio and codigo_orgao and nome_orgao
    exercicio_orcamento, _ = _exercicio(registro.get("exercicio_orcamento"))
    return {
        "fonte": FONTE_TCE_CE,
        "codigo_municipio_tce": codigo_municipio,
        "exercicio_orcamento": exercicio_orcamento,
        "codigo_orgao": codigo_orgao,
        "nome_orgao": nome_orgao,
        "codigo_tipo_unidade": tipo_unidade,
        "cnpj_orgao": _texto(registro.get("cgc_orgao"), "cgc_orgao", obrigatorio=False),
        "administracao": classificar_administracao(nome_orgao),
        "payload_bruto": dict(registro),
    }


def mapear_nota_pagamento(registro: dict[str, Any]) -> dict[str, Any]:
    codigo_municipio = _texto(registro.get("codigo_municipio"), "codigo_municipio")
    codigo_orgao = _texto(registro.get("codigo_orgao"), "codigo_orgao")
    codigo_unidade = _texto(
        registro.get("codigo_unidade_orcamentaria"), "codigo_unidade_orcamentaria"
    )
    numero_empenho = _texto(registro.get("numero_empenho"), "numero_empenho")
    numero_sub_empenho = _texto(
        registro.get("numero_sub_empenho"), "numero_sub_empenho"
    )
    numero_nota_pagamento = _texto(
        registro.get("numero_nota_pagamento"), "numero_nota_pagamento"
    )
    assert all(
        (codigo_municipio, codigo_orgao, codigo_unidade, numero_empenho,
         numero_sub_empenho, numero_nota_pagamento)
    )
    exercicio_orcamento, exercicio = _exercicio(registro.get("exercicio_orcamento"))
    competencia_documento = _competencia(registro.get("data_referencia_doc"))
    data_empenho = _data_civil(
        registro.get("data_emissao_empenho"), "data_emissao_empenho"
    )
    data_pagamento = _data_civil(
        registro.get("data_nota_pagamento"), "data_nota_pagamento"
    )
    chave_empenho = _chave_empenho(
        codigo_municipio,
        exercicio_orcamento,
        codigo_orgao,
        codigo_unidade,
        data_empenho,
        numero_empenho,
    )
    estado = _inteiro(registro.get("estado_de_estornado"), "estado_de_estornado")
    return {
        "fonte": FONTE_TCE_CE,
        "codigo_municipio_tce": codigo_municipio,
        "exercicio_orcamento": exercicio_orcamento,
        "exercicio": exercicio,
        "competencia_documento": competencia_documento,
        "competencia_pagamento": data_pagamento[:7],
        "codigo_orgao": codigo_orgao,
        "codigo_unidade_orcamentaria": codigo_unidade,
        "data_empenho": data_empenho,
        "numero_empenho": numero_empenho,
        "chave_empenho": chave_empenho,
        "numero_sub_empenho": numero_sub_empenho,
        "numero_nota_pagamento": numero_nota_pagamento,
        "chave_pagamento": chave_pagamento(
            chave_empenho, numero_sub_empenho, numero_nota_pagamento
        ),
        "numero_documento_caixa": _texto(
            registro.get("numero_documento_caixa"),
            "numero_documento_caixa",
            obrigatorio=False,
        ),
        "data_pagamento": data_pagamento,
        "valor_pagamento_centavos": valor_para_centavos(
            registro.get("valor_nota_pagamento"), "valor_nota_pagamento"
        ),
        "estado_de_estornado": estado,
        "payload_bruto": dict(registro),
    }


def mapear_orgaos(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mapear_orgao(registro) for registro in registros]


def mapear_notas_pagamentos(registros: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [mapear_nota_pagamento(registro) for registro in registros]


__all__ = [
    "ContratoDespesaTceInvalido",
    "classificar_administracao",
    "mapear_nota_pagamento",
    "mapear_notas_pagamentos",
    "mapear_orgao",
    "mapear_orgaos",
]
