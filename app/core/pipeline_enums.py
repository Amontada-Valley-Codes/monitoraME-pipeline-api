"""Estados canônicos do pipeline de pagamentos do TCE-CE."""

from enum import StrEnum


class StatusExecucaoPipeline(StrEnum):
    PENDENTE = "PENDENTE"
    EM_EXECUCAO = "EM_EXECUCAO"
    EM_VALIDACAO = "EM_VALIDACAO"
    PUBLICADO = "PUBLICADO"
    BLOQUEADO = "BLOQUEADO"
    FALHOU = "FALHOU"
    SUBSTITUIDO = "SUBSTITUIDO"


class StatusTarefaPipeline(StrEnum):
    PENDENTE = "PENDENTE"
    EM_EXECUCAO = "EM_EXECUCAO"
    AGUARDANDO_RETRY = "AGUARDANDO_RETRY"
    CONCLUIDA = "CONCLUIDA"
    FALHOU = "FALHOU"
    DLQ = "DLQ"


class SituacaoClassificacaoFornecedor(StrEnum):
    IDENTIFICADO = "IDENTIFICADO"
    SEM_CLASSIFICACAO = "SEM_CLASSIFICACAO"
    CPF = "CPF"
    DOCUMENTO_INVALIDO = "DOCUMENTO_INVALIDO"
    PENDENTE = "PENDENTE"
    INDISPONIVEL = "INDISPONIVEL"


__all__ = [
    "SituacaoClassificacaoFornecedor",
    "StatusExecucaoPipeline",
    "StatusTarefaPipeline",
]
