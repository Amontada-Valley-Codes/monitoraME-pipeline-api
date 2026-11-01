from __future__ import annotations

import unittest
from unittest.mock import patch

from app.nlp.matching.postgres_retrieval import (
    ConsultaCnae,
    buscar_contratacoes_top_k,
)


class PostgresRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queries = [
            ConsultaCnae("3514000", [0.0] * 384),
            ConsultaCnae("4321500", [0.0] * 384),
        ]

    def test_deduplica_por_contratacao_e_consolida_maior_score_por_cnae(self) -> None:
        rows_by_cnae = {
            "3514000": [
                {"source": "PNCP", "external_id": "edital-a", "segment_type": "OBJECT",
                 "segment_key": "object", "score": 0.71},
                {"source": "PNCP", "external_id": "edital-b", "segment_type": "ITEM",
                 "segment_key": "item:1", "score": 0.65},
            ],
            "4321500": [
                {"source": "PNCP", "external_id": "edital-a", "segment_type": "ITEM",
                 "segment_key": "item:2", "score": 0.89},
                {"source": "PNCP", "external_id": "edital-c", "segment_type": "OBJECT",
                 "segment_key": "object", "score": 0.62},
            ],
        }

        def buscar(_session, consulta, **_kwargs):
            return rows_by_cnae[consulta.codigo]

        with patch("app.nlp.matching.postgres_retrieval._buscar_por_cnae", side_effect=buscar):
            resultados = buscar_contratacoes_top_k(
                object(), self.queries, model_version="e5@sha", k=2
            )

        self.assertEqual([item.procurement_id for item in resultados], ["edital-a", "edital-b"])
        self.assertEqual(resultados[0].score, 0.89)
        self.assertEqual(resultados[0].cnaes, {"3514000", "4321500"})
        self.assertEqual(len(resultados[0].segments), 2)

    def test_nao_filtra_modalidade_quando_preferencia_nao_foi_informada(self) -> None:
        captured = []

        def buscar(_session, _consulta, **kwargs):
            captured.append(kwargs)
            return []

        with patch("app.nlp.matching.postgres_retrieval._buscar_por_cnae", side_effect=buscar):
            buscar_contratacoes_top_k(
                object(), self.queries[:1], model_version="e5@sha", k=3
            )

        self.assertIsNone(captured[0]["modality_codes"])
        self.assertIsNone(captured[0]["ibge_prefixes"])
        self.assertIsNone(captured[0]["derived_statuses"])

    def test_valida_k_e_dimensao_do_embedding(self) -> None:
        with self.assertRaises(ValueError):
            buscar_contratacoes_top_k(object(), self.queries, model_version="e5@sha", k=0)
        with self.assertRaises(ValueError):
            buscar_contratacoes_top_k(
                object(), [ConsultaCnae("3514000", [0.0])], model_version="e5@sha"
            )


if __name__ == "__main__":
    unittest.main()
