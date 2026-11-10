from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from app.nlp.matching.schemas import (
    BatchStatus,
    CompatibilityLevel,
    MatchingResult,
    ProcurementReference,
)
from app.nlp.profiles.schemas import (
    CompanyProfile,
    MatchingBatchRequest,
    SCHEMA_VERSION,
)


FIXTURES = Path(__file__).parent / "fixtures" / "companies.json"


class MatchingContractTests(unittest.TestCase):
    def test_fixtures_sao_compativeis_com_snapshot_prisma(self) -> None:
        profiles = json.loads(FIXTURES.read_text(encoding="utf-8"))
        batch = MatchingBatchRequest(
            batch_id=uuid4(), schema_version=SCHEMA_VERSION, companies=profiles
        )
        self.assertEqual({profile.company_size.value for profile in batch.companies}, {"MEI", "ME", "EPP", "DEMAIS"})
        self.assertGreater(len({len(profile.cnaes) for profile in batch.companies}), 1)
        self.assertTrue(all(profile.primary_cnae in {cnae.code for cnae in profile.cnaes} for profile in batch.companies))

    def test_rejeita_porte_nao_padronizado(self) -> None:
        profile = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
        profile["company_size"] = "EEP"
        with self.assertRaises(ValidationError):
            CompanyProfile.model_validate(profile)

    def test_rejeita_cnae_primario_inconsistente_e_campos_pessoais(self) -> None:
        profile = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
        profile["cnaes"][0]["is_primary"] = False
        with self.assertRaises(ValidationError):
            CompanyProfile.model_validate(profile)
        profile = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
        profile["cnpj"] = "00000000000000"
        with self.assertRaises(ValidationError):
            CompanyProfile.model_validate(profile)

    def test_business_description_opcional_ate_150_caracteres(self) -> None:
        profile = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
        profile["business_description"] = "x" * 150
        self.assertEqual(CompanyProfile.model_validate(profile).business_description, "x" * 150)
        profile["business_description"] = "x" * 151
        with self.assertRaises(ValidationError):
            CompanyProfile.model_validate(profile)
        profile["business_description"] = "   "
        self.assertIsNone(CompanyProfile.model_validate(profile).business_description)

    def test_aceita_formato_de_perfil_enviado_pelo_backend(self) -> None:
        profile = CompanyProfile.model_validate({
            "company_id": "d81b3ae3-c7e2-4c9e-863e-001fc6819ca5",
            "profile_version": "hash-do-perfil",
            "version": 3,
            "schema_version": "1",
            "company_size": "ME",
            "primary_cnae": "6201501",
            "business_description": "Criamos sistemas para pequenas empresas.",
            "cnaes": [
                {"code": "6201501", "description": "Desenvolvimento de software", "is_primary": True},
                {"code": "6202300", "description": "Software customizável", "is_primary": False},
            ],
            "location": {"municipality_code": "3550308", "state": "SP"},
            "products_and_services": [
                {"description": "Desenvolvimento de software", "keywords": ["software", "sistemas"]},
            ],
            "regions": [{"ibgeCode": "3550308", "uf": "SP"}],
            "modalities": ["6"],
        })

        self.assertEqual(profile.interested_states, ["SP"])
        self.assertEqual(profile.interested_municipalities, ["3550308"])
        self.assertEqual(profile.preferred_modality_codes, ["6"])

    def test_resultado_preserva_company_id_e_versoes(self) -> None:
        company_id = uuid4()
        result = MatchingResult(
            company_id=company_id,
            edital=ProcurementReference(
                procurement_id="PNCP:123", external_id="123", object="Compra de equipamentos",
                version="sha256:abc",
            ),
            score=82,
            data_confidence=90,
            eligibility="ELIGIBLE",
            compatibility_level=CompatibilityLevel.HIGH,
            taxonomy_version="cnae-objeto-v1",
            model_version="e5@revision-sha",
            semantic_relevance=81,
            preference_score=75,
            profile_version="profile-2",
            matching_version="matching-1",
            processing_state=BatchStatus.COMPLETED,
        )
        self.assertEqual(result.company_id, company_id)
        self.assertEqual(result.schema_version, SCHEMA_VERSION)
        self.assertEqual(result.model_version, "e5@revision-sha")

    def test_score_semantico_exige_versao_do_modelo(self) -> None:
        with self.assertRaises(ValidationError):
            MatchingResult(
                company_id=uuid4(),
                edital=ProcurementReference(
                    procurement_id="PNCP:123", external_id="123", version="v1",
                ),
                score=50, semantic_relevance=50, data_confidence=50,
                eligibility="NOT_CONFIRMED", compatibility_level="MEDIUM",
                profile_version="p1", matching_version="m1", taxonomy_version="t1",
                processing_state=BatchStatus.COMPLETED,
            )

    def test_saida_rejeita_versao_de_schema_desconhecida(self) -> None:
        with self.assertRaises(ValidationError):
            MatchingResult(
                company_id=uuid4(),
                edital=ProcurementReference(
                    procurement_id="PNCP:123", external_id="123", object="Compra",
                    version="sha256:abc",
                ),
                score=50,
                data_confidence=80,
                eligibility="NOT_CONFIRMED",
                compatibility_level=CompatibilityLevel.MEDIUM,
                profile_version="profile-1",
                schema_version="matching.v2",
                matching_version="matching-1",
                taxonomy_version="cnae-objeto-v1",
                processing_state=BatchStatus.COMPLETED,
            )


if __name__ == "__main__":
    unittest.main()
