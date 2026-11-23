from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import TestCase
from uuid import UUID

from app.nlp.matching.engine import calcular_matching
from app.nlp.matching.scoring import TaxonomyAssociation
from app.nlp.matching.schemas import ProcurementProfile
from app.nlp.profiles.schemas import CompanyProfile


NOW = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)


def empresa(cnae: str, descricao: str, categoria: str) -> CompanyProfile:
    return CompanyProfile.model_validate({
        "company_id": UUID("11111111-1111-4111-8111-111111111111"),
        "profile_version": "profile-1", "company_size": "EPP",
        "primary_cnae": cnae,
        "cnaes": [{"code": cnae, "description": descricao, "is_primary": True}],
        "location": {"municipality_code": "2304400", "state": "CE"},
        "categories": [categoria],
    })


def edital(objeto: str, item: str, categoria: str) -> ProcurementProfile:
    return ProcurementProfile(
        procurement_id="PNCP:fixture", external_id="fixture", object=objeto,
        items=[item], categories=[categoria], status="ABERTA",
        deadline=NOW + timedelta(days=10), revoked=False, annulled=False,
        suspended=False, version="fixture-1",
    )


def avaliar(
    company: CompanyProfile, opportunity: ProcurementProfile,
    *, object_similarity: float, item_similarity: float, confidence: float,
):
    return calcular_matching(
        company, opportunity, taxonomy_version="cnae-objeto-v1",
        cnae_categories={company.primary_cnae: [
            TaxonomyAssociation(opportunity.categories[0], "DIRETA", confidence)
        ]},
        semantic_score=max(object_similarity, item_similarity),
        semantic_object_score=object_similarity,
        semantic_items_score=item_similarity,
        model_version="e5@sha", as_of=NOW,
    )


class DeliveryScopeTests(TestCase):
    def test_fabrica_de_software_validada_atinge_high(self) -> None:
        company = empresa("6202300", "DESENVOLVIMENTO E LICENCIAMENTO DE PROGRAMAS DE COMPUTADOR CUSTOMIZÁVEIS", "software")
        opportunity = edital(
            "Contratação de serviços de fábrica de software, desenvolvimento, melhoria e sustentação de sistemas web e APIs",
            "Desenvolvimento e sustentação de sistemas computacionais em ambiente web",
            "software",
        )
        result = avaliar(company, opportunity, object_similarity=0.8941, item_similarity=0.8946, confidence=0.9)
        self.assertEqual(result.compatibility_level.value, "HIGH")
        self.assertIn("DELIVERY_MATCH", result.reason_codes)

    def test_refeicoes_transportadas_e_escolares_validadas_atingem_high(self) -> None:
        company = empresa("5620101", "FORNECIMENTO DE ALIMENTOS PREPARADOS PREPONDERANTEMENTE PARA EMPRESAS", "alimentacao")
        cases = (
            ("Prestação de serviços de alimentação e nutrição, preparo em unidade externa, transporte e distribuição de refeições prontas aos estudantes", 0.9081, 0.8965),
            ("Serviços de Alimentação para o fornecimento de refeições destinadas aos alunos das escolas estaduais", 0.8900, 0.8981),
            ("Serviços de Alimentação para o fornecimento de refeições destinadas aos alunos de escola estadual", 0.8900, 0.8981),
        )
        for objeto, object_similarity, item_similarity in cases:
            with self.subTest(objeto=objeto):
                result = avaliar(
                    company, edital(objeto, "Fornecimento de refeições preparadas para estudantes", "alimentacao"),
                    object_similarity=object_similarity, item_similarity=item_similarity, confidence=0.95,
                )
                self.assertEqual(result.compatibility_level.value, "HIGH")
                self.assertIn("DELIVERY_MATCH", result.reason_codes)

    def test_hardware_licenca_e_cartao_alimentacao_nao_sao_high(self) -> None:
        software = empresa("6202300", "DESENVOLVIMENTO DE PROGRAMAS DE COMPUTADOR CUSTOMIZÁVEIS", "software")
        alimentos = empresa("5620101", "FORNECIMENTO DE ALIMENTOS PREPARADOS", "alimentacao")
        cases = (
            (software, edital("Desenvolvimento de software para solução de banco de dados incluindo hardware e servidores", "Licenças e servidores", "software"), 0.91),
            (software, edital("Fornecimento de licenças Oracle Database Enterprise Edition", "Licenças Oracle", "software"), 0.91),
            (alimentos, edital("Fornecimento de cartões alimentação e gestão de créditos", "Cartões alimentação", "alimentacao"), 0.91),
            (alimentos, edital("Fornecimento de refeições prontas para estudantes", "Cartão voucher para aquisição de refeições", "alimentacao"), 0.91),
        )
        for company, opportunity, similarity in cases:
            with self.subTest(objeto=opportunity.object):
                result = avaliar(
                    company, opportunity, object_similarity=similarity,
                    item_similarity=similarity, confidence=0.95,
                )
                self.assertNotEqual(result.compatibility_level.value, "HIGH")
                self.assertNotIn("DELIVERY_MATCH", result.reason_codes)
