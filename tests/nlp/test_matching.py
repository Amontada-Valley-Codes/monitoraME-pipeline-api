from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.nlp.matching.eligibility import avaliar_elegibilidade
from app.nlp.matching.engine import calcular_matching
from app.nlp.matching.jobs import agendar_recalculo_perfil_session
from app.nlp.matching.schemas import EligibilityStatus, ProcurementProfile
from app.nlp.matching.scoring import TaxonomyAssociation, WEIGHTS, calcular_score, classificar_score
from app.nlp.profiles.builder import construir_perfil_semantico
from app.nlp.embeddings.model import EmbeddingBatch
from app.nlp.profiles.schemas import CompanyProfile
from app.nlp.repositories.matching import enfileirar_batch_session, hash_payload


NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def empresa(**updates) -> CompanyProfile:
    data = {
        "company_id": UUID("11111111-1111-4111-8111-111111111111"),
        "profile_version": "profile-1",
        "company_size": "ME",
        "primary_cnae": "6201501",
        "cnaes": [{"code": "6201501", "description": "desenvolvimento software", "is_primary": True}],
        "location": {"municipality_code": "2304400", "state": "CE"},
        "categories": ["software"],
        "participation_forms": ["INDIVIDUAL"],
    }
    data.update(updates)
    return CompanyProfile.model_validate(data)


def oportunidade(**updates) -> ProcurementProfile:
    data = {
        "procurement_id": "pncp-1",
        "external_id": "pncp-1",
        "object": "desenvolvimento de software",
        "items": ["software"],
        "categories": ["software"],
        "accepted_company_sizes": ["ME", "EPP"],
        "accepted_company_sizes_source": "PNCP_CRITERIO_OFICIAL",
        "territory_states": ["CE"],
        "territory_municipalities": None,
        "participation_form": "INDIVIDUAL",
        "status": "ABERTA",
        "deadline": NOW + timedelta(days=1),
        "revoked": False,
        "annulled": False,
        "suspended": False,
        "version": "normalized-1",
    }
    data.update(updates)
    return ProcurementProfile.model_validate(data)


class DeterministicScoringTests(TestCase):
    def test_pesos_somam_cem(self) -> None:
        self.assertEqual(sum(WEIGHTS.values()), 100)

    def test_limites_de_classificacao(self) -> None:
        expected = {49: "LOW", 50: "MEDIUM", 74: "MEDIUM", 75: "HIGH", 89: "HIGH", 90: "HIGH"}
        self.assertEqual({score: classificar_score(score) for score in expected}, expected)

    def test_calculo_reprodutivel_inclui_taxonomia_evidencias_e_timestamp(self) -> None:
        kwargs = dict(
            taxonomy_version="cnae-objeto-v2",
            cnae_categories={"6201501": [TaxonomyAssociation("software", "DIRETA", 1.0)]},
            as_of=NOW,
        )
        first = calcular_matching(empresa(), oportunidade(), **kwargs)
        second = calcular_matching(empresa(), oportunidade(), **kwargs)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertGreater(first.score, 75)
        self.assertEqual(first.data_confidence, 100)
        self.assertEqual(first.taxonomy_version, "cnae-objeto-v2")
        self.assertTrue(first.evidence)
        self.assertEqual(first.processed_at, NOW)

    def test_sinal_topk_semantico_participa_do_hibrido_e_fica_explicito(self) -> None:
        result = calcular_matching(
            empresa(), oportunidade(object="aquisição de plataforma"),
            taxonomy_version="tax-v1", cnae_categories={}, semantic_score=0.91,
            model_version="e5@sha", as_of=NOW,
        )
        self.assertEqual(result.semantic_relevance, 91)
        self.assertEqual(result.model_version, "e5@sha")
        self.assertIn("LEGAL_ELIGIBILITY_NOT_VERIFIED", result.warning_codes)
        object_evidence = next(item for item in result.evidence if item.criterion == "object")
        self.assertEqual(object_evidence.score, 0.91)
        self.assertIn("Top-K", object_evidence.details)

    def test_score_semantico_fora_da_escala_e_rejeitado(self) -> None:
        with self.assertRaises(ValueError):
            calcular_matching(
                empresa(), oportunidade(), taxonomy_version="tax-v1",
                cnae_categories={}, semantic_score=1.01, model_version="e5@sha", as_of=NOW,
            )

    def test_dado_ausente_reduz_confianca_sem_tratar_criterio_como_erro(self) -> None:
        op = oportunidade(categories=[], object=None, items=[], territory_states=None)
        result = calcular_matching(
            empresa(), op, taxonomy_version="tax-v1", cnae_categories={}, as_of=NOW
        )
        self.assertLess(result.data_confidence, 100)
        self.assertIn("CATEGORY_DATA_MISSING", result.warning_codes)
        self.assertNotEqual(result.eligibility, EligibilityStatus.INELIGIBLE)

    def test_gate_ausente_e_ineligibilidade_sao_distintos(self) -> None:
        unknown = avaliar_elegibilidade(empresa(), oportunidade(status=None), as_of=NOW)
        rejected = avaliar_elegibilidade(empresa(), oportunidade(accepted_company_sizes=["EPP"]), as_of=NOW)
        self.assertEqual(unknown.status, EligibilityStatus.NOT_CONFIRMED)
        self.assertEqual(rejected.status, EligibilityStatus.INELIGIBLE)

    def test_porte_sem_fonte_oficial_nao_decide_elegibilidade(self) -> None:
        decision = avaliar_elegibilidade(
            empresa(), oportunidade(accepted_company_sizes=["EPP"], accepted_company_sizes_source=None),
            as_of=NOW,
        )
        self.assertNotIn("COMPANY_SIZE_NOT_ACCEPTED", decision.reason_codes)
        self.assertIn("COMPANY_SIZE_UNCONFIRMED", decision.warning_codes)

    def test_sede_nao_e_preferencia_geografica_automatica(self) -> None:
        result = calcular_matching(
            empresa(), oportunidade(territory_states=["SP"], territory_municipalities=None),
            taxonomy_version="tax-v1", cnae_categories={}, as_of=NOW,
        )
        self.assertIsNone(result.preference_score)
        self.assertNotIn("preference_region", {item.criterion for item in result.evidence})

    def test_preferencia_geografica_explicita_fica_separada_do_score_de_relevancia(self) -> None:
        result = calcular_matching(
            empresa(interested_states=["CE"]), oportunidade(territory_states=["CE"]),
            taxonomy_version="tax-v1", cnae_categories={}, as_of=NOW,
        )
        self.assertEqual(result.preference_score, 100)
        self.assertEqual(result.preference_confidence, 50)
        without_preference = calcular_matching(
            empresa(), oportunidade(territory_states=["CE"]),
            taxonomy_version="tax-v1", cnae_categories={}, as_of=NOW,
        )
        self.assertEqual(result.score, without_preference.score)
        self.assertEqual(without_preference.preference_confidence, 0)
        self.assertIn("preference_region", {item.criterion for item in result.evidence})

    def test_builder_cria_um_vetor_por_cnae_e_sinaliza_descricao_ausente(self) -> None:
        profile = empresa(cnaes=[
            {"code": "6201501", "description": "software sob encomenda", "is_primary": True},
            {"code": "6202300", "description": None, "is_primary": False},
        ])
        model = SimpleNamespace(model_version="e5@sha")
        with patch(
            "app.nlp.profiles.builder.gerar_embeddings_perfis",
            return_value=EmbeddingBatch("e5@sha", "query", [[0.1] * 384, [0.2] * 384]),
        ) as encode:
            built = construir_perfil_semantico(
                profile, model, descricoes_catalogo={"6202300": "software customizavel"}
            )
        self.assertEqual([item.codigo for item in built.consultas], ["6201501", "6202300"])
        self.assertEqual(len(encode.call_args.args[1]), 2)
        self.assertEqual(built.cnaes_sem_descricao, ())

    def test_gate_rejeita_prazo_porte_e_suspensao_mas_nao_usa_territorio_como_gate(self) -> None:
        bad = oportunidade(
            deadline=NOW,
            accepted_company_sizes=["EPP"],
            territory_states=["SP"],
            participation_forms=["CONSORTIUM"],
            suspended=True,
        )
        decision = avaliar_elegibilidade(empresa(participation_forms=["CONSORTIUM"]), bad, as_of=NOW)
        self.assertEqual(decision.status, EligibilityStatus.INELIGIBLE)
        self.assertEqual(set(decision.reason_codes), {
            "OPPORTUNITY_SUSPENDED", "DEADLINE_EXPIRED", "COMPANY_SIZE_NOT_ACCEPTED",
        })

    def test_gate_reconhece_estados_derivados_pncp_sem_flags_auxiliares(self) -> None:
        for status, expected in (
            ("REVOGADA", EligibilityStatus.INELIGIBLE),
            ("ANULADA", EligibilityStatus.INELIGIBLE),
            ("SUSPENSA", EligibilityStatus.INELIGIBLE),
            ("HOMOLOGADA_TOTAL", EligibilityStatus.INELIGIBLE),
            ("ABERTURA_FUTURA", EligibilityStatus.INELIGIBLE),
            ("ENCERRANDO_EM_BREVE", EligibilityStatus.NOT_CONFIRMED),
        ):
            with self.subTest(status=status):
                decision = avaliar_elegibilidade(
                    empresa(), oportunidade(
                        status=status, revoked=None, annulled=None, suspended=None
                    ), as_of=NOW
                )
                self.assertEqual(decision.status, expected)
        near_deadline = avaliar_elegibilidade(
            empresa(), oportunidade(
                status="ENCERRANDO_EM_BREVE", revoked=None, annulled=None, suspended=None
            ), as_of=NOW
        )
        self.assertNotIn("OPEN_STATUS_UNCONFIRMED", near_deadline.warning_codes)

    def test_publicada_sem_status_derivado_aberto_fica_nao_confirmada(self) -> None:
        decision = avaliar_elegibilidade(empresa(), oportunidade(status="PUBLICADA"), as_of=NOW)
        self.assertEqual(decision.status, EligibilityStatus.NOT_CONFIRMED)

    def test_alta_requer_evidencia_de_atividade(self) -> None:
        op = oportunidade(categories=[], object=None, items=[], territory_states=["CE"])
        result = calcular_matching(
            empresa(categories=[]), op, taxonomy_version="tax-v1", cnae_categories={}, as_of=NOW
        )
        self.assertEqual(result.compatibility_level.value, "LOW")
        self.assertIn("OBJECT_DATA_MISSING", result.warning_codes)

    def test_categoria_inferida_nao_duplica_criterio_cnae(self) -> None:
        breakdown = calcular_score(
            empresa(categories=[]),
            oportunidade(),
            taxonomy_version="tax-v1",
            cnae_categories={"6201501": [TaxonomyAssociation("software", "DIRETA", 1.0)]},
        )
        self.assertNotIn("category", {item.criterion for item in breakdown.evidence})
        self.assertIn("cnae", {item.criterion for item in breakdown.evidence})

    def test_score_cnae_respeita_confianca_e_omite_relacao_condicional_nao_avaliada(self) -> None:
        company = empresa(categories=[])
        op = oportunidade(object=None, items=[], categories=["software", "hardware"])
        breakdown = calcular_score(
            company,
            op,
            taxonomy_version="tax-v2",
            cnae_categories={"6201501": [
                TaxonomyAssociation("software", "RELACIONADA", 0.4),
                TaxonomyAssociation("hardware", "CONDICIONAL", 0.95, "exige certificação"),
            ]},
        )
        cnae_evidence = next(item for item in breakdown.evidence if item.criterion == "cnae")
        self.assertEqual(cnae_evidence.score, 0.2)
        self.assertIn("CONDICIONAL", cnae_evidence.details)
        self.assertIn("CNAE_CONDITIONAL_UNRESOLVED", breakdown.warning_codes)

    def test_agendamento_de_perfil_e_idempotente_por_versao(self) -> None:
        session = SimpleNamespace()
        with patch("app.nlp.matching.jobs.agendar_job_session", return_value=(42, True)) as schedule:
            job_id, created = agendar_recalculo_perfil_session(session, empresa(), now=NOW)
            first_dedupe = schedule.call_args.args[1].dedupe_key
            agendar_recalculo_perfil_session(session, empresa(), now=NOW)
            second_dedupe = schedule.call_args.args[1].dedupe_key
        self.assertEqual((job_id, created), (42, True))
        job = schedule.call_args.args[1]
        self.assertEqual(job.job_type, "nlp_matching")
        self.assertEqual(first_dedupe, second_dedupe)
        self.assertIn("profile-1", job.scope_key)
        self.assertIn("hybrid-matching-v2", job.scope_key)

    def test_batch_async_guarda_snapshot_minimo_e_enfileira_job_deduplicavel(self) -> None:
        session = MagicMock()
        session.get.return_value = None
        profile = empresa()
        with patch("app.nlp.repositories.matching.agendar_job_session", return_value=(91, True)) as schedule:
            batch, created = enfileirar_batch_session(
                session, batch_id="batch-1", profiles=[profile],
            )
        self.assertTrue(created)
        self.assertEqual(batch.status, "PENDING")
        self.assertEqual(batch.payload_hash, hash_payload([profile]))
        self.assertEqual(batch.profiles[0]["company_id"], str(profile.company_id))
        self.assertEqual(schedule.call_args.args[1].job_type, "nlp_matching_batch")

    def test_batch_completed_igual_nao_enfileira_job_repetido(self) -> None:
        profile = empresa()
        batch = SimpleNamespace(payload_hash=hash_payload([profile]), status="COMPLETED")
        session = MagicMock()
        session.get.return_value = batch
        with patch("app.nlp.repositories.matching.agendar_job_session") as schedule:
            same, created = enfileirar_batch_session(
                session, batch_id="batch-1", profiles=[profile],
            )
        self.assertIs(same, batch)
        self.assertFalse(created)
        schedule.assert_not_called()
