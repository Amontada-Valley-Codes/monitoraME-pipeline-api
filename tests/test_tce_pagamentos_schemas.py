from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.api.schemas.pagamentos import TcePagamentosResponse
from app.core.config import NATUREZAS_DESPESA_MONITORADAS


def _payload_valido() -> dict[str, object]:
    return {
        "escopo": {
            "codigo_municipio_tce": "004",
            "codigo_municipio_ibge": "2300200",
            "municipio": "Acaraú",
            "exercicio_orcamento": 202500,
            "competencia_corte": "2026-09",
            "medida_financeira": "valor_nota_pagamento",
            "porte_publico": "ME",
        },
        "totais": {
            "total_financeiro_centavos": 13_531_152_944,
            "total_me_centavos": 0,
            "participacao_me_percentual": None,
        },
        "elementos": [
            {
                "codigo": codigo,
                "nome": nome,
                "total_financeiro_centavos": 0,
                "total_me_centavos": 0,
                "participacao_me_percentual": None,
            }
            for codigo, nome in NATUREZAS_DESPESA_MONITORADAS.items()
        ],
        "reconciliacao": [
            {"categoria": categoria, "valor_centavos": 0}
            for categoria in (
                "ME", "EPP", "DEMAIS", "NAO_IDENTIFICADO", "CPF", "DOCUMENTO_INVALIDO"
            )
        ],
        "qualidade": {
            "status": "APROVADA",
            "pagamentos_processados": 11_956,
            "pagamentos_orfaos": 0,
            "duplicidades_conflitantes": 0,
            "classificacoes_pendentes": 0,
            "financeiro_reconciliado": True,
        },
        "referencia_cadastral": {
            "fonte": "RECEITA_FEDERAL",
            "referencia": "2026-09",
            "consultado_em": "2026-10-20T12:00:00Z",
            "procedencia_temporal": "RETRATO_MENSAL",
        },
        "versao_metodologica": "tce-pagamentos-v1",
        "atualizado_em": "2026-10-20T12:00:00Z",
    }


class TcePagamentosSchemasTest(unittest.TestCase):
    def test_valida_e_serializa_contrato_completo(self) -> None:
        resposta = TcePagamentosResponse.model_validate(_payload_valido())

        serializado = resposta.model_dump(mode="json")

        self.assertEqual(serializado["escopo"]["exercicio_orcamento"], 202500)
        self.assertEqual(serializado["qualidade"]["status"], "APROVADA")
        self.assertEqual(len(serializado["elementos"]), 7)
        self.assertEqual(len(serializado["reconciliacao"]), 6)
        self.assertEqual(serializado["atualizado_em"], "2026-10-20T12:00:00Z")

    def test_rejeita_campos_extras_em_objetos_aninhados(self) -> None:
        payload = _payload_valido()
        payload["qualidade"]["campo_extra"] = True

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)

    def test_rejeita_exercicio_fora_do_formato_aaaa00(self) -> None:
        for exercicio in (2025, 202501):
            payload = _payload_valido()
            payload["escopo"]["exercicio_orcamento"] = exercicio
            with self.subTest(exercicio=exercicio), self.assertRaises(ValidationError):
                TcePagamentosResponse.model_validate(payload)

    def test_rejeita_status_de_qualidade_nao_previsto(self) -> None:
        payload = _payload_valido()
        payload["qualidade"]["status"] = "DESCONHECIDA"

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)

    def test_rejeita_versao_metodologica_diferente_da_contratada(self) -> None:
        payload = _payload_valido()
        payload["versao_metodologica"] = "tce-pagamentos-v2"

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)

    def test_aceita_os_status_de_qualidade_aprovados(self) -> None:
        for status in ("APROVADA", "BLOQUEADA", "PARCIAL"):
            payload = _payload_valido()
            payload["qualidade"]["status"] = status

            with self.subTest(status=status):
                resposta = TcePagamentosResponse.model_validate(payload)
                self.assertEqual(resposta.qualidade.status.value, status)

    def test_rejeita_percentual_fora_da_escala(self) -> None:
        payload = _payload_valido()
        payload["totais"]["participacao_me_percentual"] = 100.01

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)

    def test_exige_os_sete_elementos_sem_duplicidade(self) -> None:
        payload = _payload_valido()
        payload["elementos"][-1] = dict(payload["elementos"][0])

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)

    def test_exige_todas_as_categorias_de_reconciliacao(self) -> None:
        payload = _payload_valido()
        payload["reconciliacao"][-1] = dict(payload["reconciliacao"][0])

        with self.assertRaises(ValidationError):
            TcePagamentosResponse.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
