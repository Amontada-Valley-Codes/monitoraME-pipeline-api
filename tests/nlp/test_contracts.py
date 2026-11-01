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

    def test_resultado_preserva_company_id_e_versoes(self) -> None:
        company_id = uuid4()
        result = MatchingResult(
            company_id=company_id,
            edital=ProcurementReference(
                procurement_id="PNCP:123", external_id="123", object="Compra de equipamentos",
                version="sha256:abc",
            ),
            score=0.82,
            compatibility_level=CompatibilityLevel.HIGH,
            profile_version="profile-2",
            matching_version="matching-1",
            processing_state=BatchStatus.COMPLETED,
        )
        self.assertEqual(result.company_id, company_id)
        self.assertEqual(result.schema_version, SCHEMA_VERSION)

    def test_saida_rejeita_versao_de_schema_desconhecida(self) -> None:
        with self.assertRaises(ValidationError):
            MatchingResult(
                company_id=uuid4(),
                edital=ProcurementReference(
                    procurement_id="PNCP:123", external_id="123", object="Compra",
                    version="sha256:abc",
                ),
                score=0.5,
                compatibility_level=CompatibilityLevel.MEDIUM,
                profile_version="profile-1",
                schema_version="matching.v2",
                matching_version="matching-1",
                processing_state=BatchStatus.COMPLETED,
            )


if __name__ == "__main__":
    unittest.main()
