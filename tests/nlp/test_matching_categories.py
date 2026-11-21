from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.nlp.matching.categories import classificar_categorias_cnaes, classificar_categorias_edital
from app.nlp.matching.engine import calcular_matching
from app.nlp.matching.pipeline import SEGMENT_QUERY_CHUNK_SIZE, _carregar_segmentos_evidencia
from app.nlp.matching.scoring import TaxonomyAssociation
from app.nlp.profiles.schemas import CnaeProfile
from tests.nlp.test_matching import empresa, oportunidade, NOW


class MatchingCategoriesTests(unittest.TestCase):
    def test_inferencia_por_descricao_preserva_categorias_explicitas(self) -> None:
        profile = empresa(categories=["consultoria-gestao"]).model_copy(update={
            "cnaes": [
                CnaeProfile(code="6202300", description="Desenvolvimento e licenciamento de programas de computador customizáveis", is_primary=True),
                CnaeProfile(code="7490104", description="Atividades de intermediação e agenciamento de serviços", is_primary=False),
                CnaeProfile(code="8291100", description="Atividades de cobranças e informações cadastrais", is_primary=False),
            ],
        })

        classified = classificar_categorias_cnaes(
            profile, associations={}, active_categories={"software", "consultoria-gestao"},
        )

        self.assertEqual(classified.categories, ("consultoria-gestao", "software"))
        self.assertEqual(classified.inferred_from_cnaes, {"software": ("6202300",)})
        self.assertEqual(classified.unclassified_cnaes, ("7490104", "8291100"))
        self.assertEqual(profile.categories, ["consultoria-gestao"])

    def test_mapa_ativo_prevalece_e_relacao_condicional_nao_confirma(self) -> None:
        profile = empresa(categories=[]).model_copy(update={
            "cnaes": [
                CnaeProfile(code="6204000", description="Consultoria em tecnologia da informação", is_primary=True),
                CnaeProfile(code="7490104", description="Intermediação de software", is_primary=False),
            ],
        })
        classified = classificar_categorias_cnaes(
            profile,
            associations={
                "6204000": [TaxonomyAssociation("software", "DIRETA", 0.9)],
                "7490104": [TaxonomyAssociation("software", "CONDICIONAL", 0.95, "verificar objeto")],
            },
            active_categories={"software"},
        )
        self.assertEqual(classified.categories, ("software",))
        self.assertEqual(classified.inferred_from_cnaes, {"software": ("6204000",)})
        self.assertEqual(classified.unclassified_cnaes, ("7490104",))

    def test_categoria_inferida_sozinha_nao_produz_high(self) -> None:
        profile = empresa(categories=[])
        associations = {"6201501": [TaxonomyAssociation("software", "DIRETA", 0.95)]}
        classified = classificar_categorias_cnaes(
            profile, associations=associations, active_categories={"software"},
        )
        result = calcular_matching(
            profile.model_copy(update={"categories": list(classified.categories)}),
            oportunidade(object=None, complementary_text=None, items=[], categories=["software"]),
            taxonomy_version="cnae-objeto-v1", cnae_categories=associations, as_of=NOW,
        )
        self.assertEqual(result.compatibility_level.value, "LOW")
        self.assertIn("CATEGORY_MATCH", result.reason_codes)
        self.assertIn("CNAE_MATCH", result.reason_codes)

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

    def test_item_incidental_nao_classifica_objeto_especifico(self) -> None:
        for objeto in (
            "Aquisição de máquinas fotográficas profissionais com acessórios",
            "Consulta e busca de informações cadastrais de pessoas físicas e jurídicas",
        ):
            with self.subTest(objeto=objeto):
                self.assertEqual(
                    classificar_categorias_edital(
                        objeto, ["Software de apoio e consultoria em tecnologia da informação"],
                        categorias_ativas={"software"},
                    ),
                    [],
                )

    def test_item_classifica_quando_objeto_e_generico(self) -> None:
        self.assertEqual(
            classificar_categorias_edital(
                "Contratação de itens conforme termo de referência",
                ["Desenvolvimento de software sob encomenda"],
                categorias_ativas={"software"},
            ),
            ["software"],
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
