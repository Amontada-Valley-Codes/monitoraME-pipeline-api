"""Coleta, normaliza e publica notas_pagamentos por orçamento e competência."""
from __future__ import annotations

from datetime import date, datetime, timezone
import logging
import re

from app.pipeline.cleaners import tce_despesas as cleaner
from app.pipeline.ingestion import tce
from app.pipeline.persistence import tce_pagamentos as persistence


logger = logging.getLogger(__name__)


def executar_ingestao_competencia_pagamentos_tce(
    competencia: str,
    *,
    exercicio_orcamento: int,
    codigo_municipio_tce: str,
) -> persistence.ResultadoPublicacaoPagamentos:
    """Importa o snapshot mensal; competência documental não limita o orçamento."""
    try:
        date.fromisoformat(f"{competencia}-01")
    except (TypeError, ValueError) as error:
        raise ValueError("Competência inválida; use YYYY-MM.") from error
    if not re.fullmatch(r"[0-9]{3}", codigo_municipio_tce):
        raise ValueError("Código do município TCE deve conter três dígitos.")
    if isinstance(exercicio_orcamento, bool) or not isinstance(exercicio_orcamento, int) or not 1900 <= exercicio_orcamento <= 9999:
        raise ValueError("Exercício orçamentário deve ser um ano válido de quatro dígitos.")

    inicio = datetime.now(timezone.utc)
    logger.info(
        "Ingestão de notas_pagamentos iniciada",
        extra={
            "competencia": competencia,
            "exercicio_orcamento": exercicio_orcamento * 100,
            "codigo_municipio": codigo_municipio_tce,
        },
    )
    try:
        brutos = tce.buscar_notas_pagamentos(
            competencia,
            exercicio_orcamento=exercicio_orcamento,
            codigo_municipio=codigo_municipio_tce,
        )
        pagamentos = cleaner.mapear_notas_pagamentos(
            brutos,
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio_orcamento,
            competencia=competencia,
        )
    except Exception as error:
        logger.error(
            "Falha na coleta ou normalização de notas_pagamentos do TCE-CE",
            extra={
                "competencia": competencia,
                "exercicio_orcamento": exercicio_orcamento * 100,
                "codigo_municipio": codigo_municipio_tce,
                "error_type": type(error).__name__,
            },
            exc_info=True,
        )
        persistence.registrar_lote_rejeitado(
            codigo_municipio_tce=codigo_municipio_tce,
            exercicio_orcamento=exercicio_orcamento,
            competencia=competencia,
            erro=f"{type(error).__name__}: {error}",
            iniciado_em=inicio,
        )
        raise

    resultado = persistence.publicar_lote(
        codigo_municipio_tce=codigo_municipio_tce,
        exercicio_orcamento=exercicio_orcamento,
        competencia=competencia,
        pagamentos=pagamentos,
        iniciado_em=inicio,
    )
    logger.log(
        logging.INFO if resultado.status == persistence.STATUS_PUBLICADO else logging.WARNING,
        "Ingestão de notas_pagamentos concluída",
        extra={
            "competencia": competencia,
            "exercicio_orcamento": exercicio_orcamento * 100,
            "codigo_municipio": codigo_municipio_tce,
            "run_id": resultado.run_id,
            "status": resultado.status,
            "pagamentos_count": resultado.quantidade_pagamentos,
            "problems_count": len(resultado.problemas),
            "previous_run_id": resultado.run_anterior_id,
        },
    )
    return resultado


__all__ = ["executar_ingestao_competencia_pagamentos_tce"]
