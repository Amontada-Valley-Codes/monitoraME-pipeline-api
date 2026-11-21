from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.nlp.matching.categories import classificar_categorias_edital
from app.nlp.matching.engine import calcular_matching
from app.nlp.matching.pipeline import SEGMENT_QUERY_CHUNK_SIZE, _carregar_segmentos_evidencia
from app.nlp.matching.scoring import TaxonomyAssociation
from tests.nlp.test_matching import empresa, oportunidade, NOW


class MatchingCategoriesTests(unittest.TestCase):
    def test_classifica_apenas_categoria_com_sinal_explicito_e_ativa(self) -> None:
        categories = {"software", "alimentacao"}
        self.assertEqual(
            classificar_categorias_edital(
                "Licenciamento de software", ["Instalação de aplicativo"],
                categorias_ativas=categories,
            ),
            ["software"],
        )
        self.assertEqual(
            classificar_categorias_edital(
                "Serviços de manutenção de sistema de climatização", [],
                categorias_ativas=categories,
            ),
            [],
        )
        self.assertEqual(
            classificar_categorias_edital(
                "Distribuição de energia elétrica", [], categorias_ativas=categories,
            ),
            [],
        )

    def test_segmentos_sao_consultados_em_blocos_sem_perder_chaves(self) -> None:
        session = MagicMock()
        keys = [("PNCP", str(i), "OBJECT", "object") for i in range(SEGMENT_QUERY_CHUNK_SIZE + 1)]
        session.scalars.side_effect = [
            [SimpleNamespace(source="PNCP", external_id="0", segment_type="OBJECT", segment_key="object")],
            [SimpleNamespace(source="PNCP", external_id=str(SEGMENT_QUERY_CHUNK_SIZE), segment_type="OBJECT", segment_key="object")],
        ]

        stored = _carregar_segmentos_evidencia(session, [*keys, keys[0]])

        self.assertEqual(session.scalars.call_count, 2)
        self.assertEqual(set(stored), {keys[0], keys[-1]})
        for call in session.scalars.call_args_list:
            statement = call.args[0]
            self.assertLessEqual(len(statement._where_criteria[0].right.value), SEGMENT_QUERY_CHUNK_SIZE)

    def test_categoria_confirmada_permite_high_com_demais_evidencias(self) -> None:
        categories = classificar_categorias_edital(
            "Desenvolvimento de software", ["Software sob encomenda"],
            categorias_ativas={"software"},
        )
        result = calcular_matching(
            empresa(), oportunidade(categories=categories, complementary_text="desenvolvimento software"),
            taxonomy_version="cnae-objeto-v1",
            cnae_categories={"6201501": [TaxonomyAssociation("software", "DIRETA", 1.0)]},
            semantic_score=1.0, semantic_object_score=1.0,
            semantic_complementary_score=1.0, semantic_items_score=1.0,
            model_version="e5@sha", as_of=NOW,
        )
        self.assertEqual(result.compatibility_level.value, "HIGH")


if __name__ == "__main__":
    unittest.main()
