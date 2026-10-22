"""Fluxo síncrono completo de publicação dos pagamentos do TCE-CE."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core import database
from app.pipeline import pagamentos as dominio
from app.pipeline import tce_despesas
from app.pipeline.cleaners import tce_pagamentos as cleaner
from app.pipeline.ingestion import tce
from app.pipeline.persistence import tce_despesas as despesas_persistence
from app.pipeline.persistence import tce_pagamentos as persistence
from app.pipeline.portes_historicos import meses


class PublicacaoPagamentosBloqueada(RuntimeError):
    def __init__(self, resultado: dominio.ResultadoPagamentos):
        super().__init__("Publicação bloqueada por inconsistências na conciliação de pagamentos.")
        self.resultado = resultado


def executar_pipeline_pagamentos(
    *,
    codigo_municipio_tce: str,
    exercicio_orcamento: int,
    competencia_corte: str,
    atualizar_empenhos: bool = True,
) -> dominio.ResultadoPagamentos:
    municipio = database.localizar_municipio_tce(codigo_municipio_tce)
    if municipio is None:
        raise ValueError("Município TCE não possui associação local com o IBGE.")
    ano_orcamento, resto = divmod(int(exercicio_orcamento), 100)
    if resto or ano_orcamento < 1000:
        raise ValueError("exercicio_orcamento deve usar o formato AAAA00.")
    inicio = f"{ano_orcamento:04d}-01"
    if competencia_corte < inicio:
        raise ValueError("competencia_corte não pode ser anterior ao exercício orçamentário.")

    if atualizar_empenhos:
        for competencia in meses(inicio, f"{ano_orcamento:04d}-12"):
            publicacao = tce_despesas.executar_ingestao_competencia_tce(
                competencia,
                codigo_municipio_tce=codigo_municipio_tce,
            )
            if publicacao.status != despesas_persistence.STATUS_PUBLICADO:
                raise RuntimeError(
                    f"Empenhos da competência {competencia} não foram publicados."
                )

    paginas: list[dict[str, Any]] = []

    def coletor(competencia: str):
        def registrar(endpoint: str, start_index: int, registros: list[Any]) -> None:
            paginas.append({
                "endpoint": endpoint,
                "competencia": competencia,
                "start_index": start_index,
                "quantidade": len(registros),
                "registros": registros,
            })
        return registrar

    orgaos_brutos = tce.buscar_orgaos(
        exercicio_orcamento,
        codigo_municipio=codigo_municipio_tce,
        ao_coletar_pagina=coletor(""),
    )
    orgaos = cleaner.mapear_orgaos(orgaos_brutos)
    pagamentos: list[dict[str, Any]] = []
    for competencia in meses(inicio, competencia_corte):
        brutos = tce.buscar_notas_pagamentos(
            competencia,
            exercicio_orcamento,
            codigo_municipio=codigo_municipio_tce,
            ao_coletar_pagina=coletor(competencia),
        )
        pagamentos.extend(cleaner.mapear_notas_pagamentos(brutos))

    empenhos = despesas_persistence.listar_empenhos_liquidos_publicados(
        codigo_municipio_tce=codigo_municipio_tce,
        data_inicial=date(ano_orcamento, 1, 1),
        data_final=date(ano_orcamento, 12, 31),
        somente_naturezas_consideradas=False,
    )
    cnpjs = [str(item["cnpj_fornecedor"]) for item in empenhos if item.get("cnpj_fornecedor")]
    competencias_pagamento = [str(item["competencia_pagamento"]) for item in pagamentos]
    classificador, referencia = persistence.montar_classificador_cadastral(
        cnpjs, competencias_pagamento
    )
    resultado = dominio.processar_pagamentos(
        codigo_municipio_tce=str(municipio["codigo_municipio_tce"]),
        codigo_municipio_ibge=str(municipio["codigo_municipio_ibge"]),
        municipio=str(municipio["nome"]),
        exercicio_orcamento=exercicio_orcamento,
        competencia_corte=competencia_corte,
        orgaos=orgaos,
        pagamentos=pagamentos,
        empenhos=empenhos,
        classificar_cnpj=classificador,
        referencia_cadastral=referencia,
    )
    if not resultado.publicavel:
        raise PublicacaoPagamentosBloqueada(resultado)
    persistence.publicar_pagamentos(
        resultado=resultado,
        orgaos=orgaos,
        pagamentos=pagamentos,
        paginas=paginas,
    )
    return resultado


__all__ = ["PublicacaoPagamentosBloqueada", "executar_pipeline_pagamentos"]
