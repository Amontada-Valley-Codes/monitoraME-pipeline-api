"""Coleta explicita de documentos TCE usados na auditoria de pagamentos."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re

from app.pipeline.ingestion import tce
from app.pipeline.referencias import referencias_entre


def coletar_documentos_prefeitura(
    *,
    codigo_municipio: str,
    exercicio: int,
    referencia_final: str,
) -> dict:
    """Consulta o TCE e devolve uma coleta bruta, sem executar analise."""
    codigo_municipio = codigo_municipio.strip()
    if not re.fullmatch(r"[0-9]{3}", codigo_municipio):
        raise ValueError("Codigo do municipio deve conter tres digitos.")
    referencias = referencias_entre(f"{exercicio:04d}-01", referencia_final)
    if referencia_final < f"{exercicio:04d}-12":
        raise ValueError("Referencia final deve cobrir pelo menos dezembro do exercicio.")

    inicio = datetime.now(timezone.utc).isoformat()
    orgaos = tce.buscar_documentos_exercicio(
        "orgaos",
        codigo_municipio=codigo_municipio,
        exercicio=exercicio,
    )
    unidades = tce.buscar_documentos_exercicio(
        "unidades_orcamentarias",
        codigo_municipio=codigo_municipio,
        exercicio=exercicio,
    )
    unidades_por_orgao: dict[str, list[str]] = defaultdict(list)
    for item in unidades:
        codigo_orgao = str(item.get("codigo_orgao", "")).strip()
        codigo_unidade = str(item.get("codigo_unidade_orcamentaria", ""))
        if (
            codigo_orgao
            and codigo_unidade.strip()
            and codigo_unidade not in unidades_por_orgao[codigo_orgao]
        ):
            unidades_por_orgao[codigo_orgao].append(codigo_unidade)

    codigos_orgaos = sorted(
        {str(item.get("codigo_orgao", "")).strip() for item in orgaos} - {""}
    )
    if not codigos_orgaos or not unidades_por_orgao:
        raise RuntimeError(
            f"Cadastro de orgaos/unidades vazio para {codigo_municipio}/{exercicio}; "
            "pagamentos nao foram tratados como competencia vazia."
        )

    empenhos: list[dict] = []
    pagamentos: list[dict] = []
    consultas_pagamentos: list[tuple[dict, str]] = []
    for referencia in referencias:
        parametros = {
            "codigo_municipio": codigo_municipio,
            "exercicio": exercicio,
            "competencia": referencia,
        }
        if referencia[:4] == str(exercicio):
            empenhos.extend(
                tce.buscar_documentos_exercicio("notas_empenhos", **parametros)
            )
        for codigo_orgao in codigos_orgaos:
            consultas_pagamentos.append((parametros, codigo_orgao))

    def buscar_particao_pagamentos(
        consulta: tuple[dict, str],
    ) -> tuple[list[dict], int, int]:
        parametros, codigo_orgao = consulta
        registros = tce.buscar_documentos_exercicio(
            "notas_pagamentos",
            **parametros,
            codigo_orgao=codigo_orgao,
        )
        if len(registros) < 1000:
            return registros, 1, 0

        registros_unidades: list[dict] = []
        unidades_orgao = unidades_por_orgao.get(codigo_orgao, [])
        if not unidades_orgao:
            raise RuntimeError(f"Orgao {codigo_orgao} sem unidades para subdividir a consulta.")
        for codigo_unidade in unidades_orgao:
            parcela = tce.buscar_documentos_exercicio(
                "notas_pagamentos",
                **parametros,
                codigo_orgao=codigo_orgao,
                codigo_unidade_orcamentaria=codigo_unidade,
            )
            if len(parcela) > 1000:
                raise RuntimeError(
                    f"Particao {codigo_orgao}/{codigo_unidade.strip()} excedeu 1000 notas; "
                    "a coleta nao pode ser considerada completa."
                )
            registros_unidades.extend(parcela)
        return registros_unidades, len(unidades_orgao) + 1, 1

    consultas_executadas = 0
    orgaos_subdivididos = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        for registros, chamadas, subdividido in executor.map(
            buscar_particao_pagamentos,
            consultas_pagamentos,
        ):
            pagamentos.extend(registros)
            consultas_executadas += chamadas
            orgaos_subdivididos += subdividido

    return {
        "codigo_municipio": codigo_municipio,
        "exercicio": exercicio,
        "referencia_final": referencia_final,
        "coleta_iniciada_em": inicio,
        "coleta_concluida_em": datetime.now(timezone.utc).isoformat(),
        "particionamento_pagamentos": {
            "dimensoes": [
                "codigo_orgao",
                "codigo_unidade_orcamentaria_se_necessario",
            ],
            "orgaos": len(codigos_orgaos),
            "unidades_orcamentarias": sum(
                len(items) for items in unidades_por_orgao.values()
            ),
            "consultas_planejadas": len(consultas_pagamentos),
            "consultas_executadas": consultas_executadas,
            "orgaos_subdivididos_por_limite": orgaos_subdivididos,
        },
        "orgaos": orgaos,
        "unidades_orcamentarias": unidades,
        "empenhos": empenhos,
        "pagamentos": pagamentos,
    }


__all__ = ["coletar_documentos_prefeitura", "referencias_entre"]
