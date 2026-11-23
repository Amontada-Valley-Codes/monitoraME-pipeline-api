from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.nlp.matching.pipeline import _executar_matching_em_blocos
from app.nlp.matching.postgres_retrieval import ConsultaCnae, comparar_contratacoes_bloco
from app.nlp.profiles.schemas import CompanyProfile
from app.nlp.repositories.matching import hash_payload


def _profile() -> CompanyProfile:
    return CompanyProfile.model_validate({
        "company_id": UUID("11111111-1111-4111-8111-111111111111"),
        "profile_version": "profile-1", "company_size": "ME",
        "primary_cnae": "6201501",
        "cnaes": [{"code": "6201501", "description": "desenvolvimento software", "is_primary": True}],
        "location": {"municipality_code": "2304400", "state": "CE"},
    })


class ExhaustiveRetrievalTests(TestCase):
    def test_avalia_todos_os_editais_do_bloco_inclusive_sem_vetor(self) -> None:
        row = SimpleNamespace(
            source="PNCP", external_id="a", segment_type="OBJECT",
            segment_key="object", vector=[1.0] + [0.0] * 383,
        )
        session = MagicMock()
        session.execute.return_value = [row]
        candidates = comparar_contratacoes_bloco(
            session, [ConsultaCnae("6201501", [1.0] + [0.0] * 383)],
            model_version="e5@sha", opportunity_keys=[("PNCP", "a"), ("PNCP", "b")],
        )
        self.assertEqual([item.procurement_id for item in candidates], ["a", "b"])
        self.assertAlmostEqual(candidates[0].score, 1.0)
        self.assertEqual(candidates[1].segments, [])


class MatchingBlockProgressTests(TestCase):
    def test_retoma_no_primeiro_bloco_nao_confirmado(self) -> None:
        profile = _profile()
        company_id = str(profile.company_id)
        keys = [["PNCP", f"edital-{i}"] for i in range(201)]
        batch = SimpleNamespace(
            payload_hash=hash_payload([profile]), status="PENDING",
            opportunity_keys=keys, matching_offsets={company_id: 200}, updated_at=None,
            matching_model_version=None, matching_taxonomy_version=None,
        )
        session = MagicMock()
        session.get.return_value = batch
        session.execute.return_value.all.return_value = []
        session.execute.return_value.first.return_value = None
        session.execute.return_value.mappings.return_value = []
        session.scalars.return_value.all.return_value = []
        context = MagicMock()
        context.__enter__.return_value = session
        semantic = SimpleNamespace(
            consultas=[ConsultaCnae("6201501", [1.0] + [0.0] * 383)],
            model_version="e5@sha", cnaes_sem_descricao=[],
        )
        category = SimpleNamespace(categories=[], inferred_from_cnaes={}, unclassified_cnaes=[])
        job = SimpleNamespace(id=1, lease_token="lease")
        with (
            patch("app.nlp.matching.pipeline.orm.main_session", return_value=context),
            patch("app.nlp.matching.pipeline.construir_perfil_semantico", return_value=semantic),
            patch("app.nlp.matching.pipeline.classificar_categorias_cnaes", return_value=category),
            patch("app.nlp.matching.pipeline.comparar_contratacoes_bloco", return_value=[]) as compare,
            patch("app.nlp.matching.pipeline._avaliar_candidatos", return_value=[]),
            patch("app.nlp.matching.pipeline.salvar_resultados_session") as save,
            patch("app.nlp.matching.pipeline._renovar_lease_matching"),
            patch("app.nlp.matching.pipeline.concluir_job_session", return_value=True),
        ):
            results, created = _executar_matching_em_blocos(
                [profile], batch_id="batch-1", model=MagicMock(),
                instant=datetime(2026, 11, 22, tzinfo=timezone.utc), leased_job=job,
            )
        self.assertEqual((results, created), ([], True))
        self.assertEqual(compare.call_args.kwargs["opportunity_keys"], [("PNCP", "edital-200")])
        self.assertEqual(batch.matching_offsets[company_id], 201)
        self.assertEqual([call.kwargs["finalize"] for call in save.call_args_list[:1]], [False])
        self.assertNotIn("finalize", save.call_args_list[-1].kwargs)
