import unittest

from app.core.pipeline_enums import (
    SituacaoClassificacaoFornecedor,
    StatusExecucaoPipeline,
    StatusTarefaPipeline,
)


def _valores(enum):
    return tuple(item.value for item in enum)


class PipelineEnumsTest(unittest.TestCase):
    def test_status_execucao_pipeline_possui_contrato_aprovado(self) -> None:
        self.assertEqual(
            _valores(StatusExecucaoPipeline),
            (
                "PENDENTE",
                "EM_EXECUCAO",
                "EM_VALIDACAO",
                "PUBLICADO",
                "BLOQUEADO",
                "FALHOU",
                "SUBSTITUIDO",
            ),
        )

    def test_status_tarefa_pipeline_possui_contrato_aprovado(self) -> None:
        self.assertEqual(
            _valores(StatusTarefaPipeline),
            (
                "PENDENTE",
                "EM_EXECUCAO",
                "AGUARDANDO_RETRY",
                "CONCLUIDA",
                "FALHOU",
                "DLQ",
            ),
        )

    def test_situacao_classificacao_fornecedor_possui_contrato_aprovado(self) -> None:
        self.assertEqual(
            _valores(SituacaoClassificacaoFornecedor),
            (
                "IDENTIFICADO",
                "SEM_CLASSIFICACAO",
                "CPF",
                "DOCUMENTO_INVALIDO",
                "PENDENTE",
                "INDISPONIVEL",
            ),
        )
