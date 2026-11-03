from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.pipeline import analisys, tce_observability


class MetadadosCargaTceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agora = datetime(2025, 2, 10, 12, 0, tzinfo=timezone.utc)

    def _metadados(self, **sobrescritos):
        argumentos = {
            "data_inicial": date(2025, 1, 1),
            "data_final": date(2025, 1, 31),
            "competencias_solicitadas": ["2025-01"],
            "publicacoes": [{
                "codigo_municipio_tce": "010",
                "competencia": "2025-01",
                "publicado_em": self.agora - timedelta(minutes=10),
            }],
            "falhas": [],
            "registros": [],
            "municipios_no_escopo": 1,
            "agora": self.agora,
        }
        argumentos.update(sobrescritos)
        return tce_observability.construir_metadados_carga(**argumentos)

    def test_publicacao_sem_movimento_e_dado_disponivel_com_zero_conhecido(self) -> None:
        metadados = self._metadados()

        self.assertTrue(metadados["data_available"])
        self.assertEqual(metadados["data_as_of"], "2025-01-31")
        self.assertIsNotNone(metadados["last_successful_load_at"])
        self.assertFalse(metadados["is_stale"])
        self.assertEqual(metadados["quality"]["unknown_origin"]["value_centavos"], 0)
        self.assertIsNone(metadados["quality"]["unknown_origin"]["percent"])
        self.assertIsNone(
            metadados["quality"]["historical_size_classification_coverage"]["percent"]
        )

    def test_falha_mais_recente_preserva_snapshot_e_sinaliza_defasagem(self) -> None:
        metadados = self._metadados(
            falhas=[{
                "codigo_municipio_tce": "010",
                "competencia": "2025-01",
                "finalizado_em": self.agora,
            }],
        )

        self.assertTrue(metadados["data_available"])
        self.assertTrue(metadados["is_stale"])
        self.assertIn("LATEST_LOAD_FAILED", metadados["stale_reason"])
        self.assertEqual(metadados["data_as_of"], "2025-01-31")
        self.assertEqual(
            metadados["quality"]["alerts"][0]["code"],
            "LATEST_LOAD_FAILED",
        )

    def test_publicacao_sem_timestamp_nao_e_reportada_como_atual(self) -> None:
        metadados = self._metadados(publicacoes=[{
            "codigo_municipio_tce": "010",
            "competencia": "2025-01",
            "publicado_em": None,
        }])

        self.assertTrue(metadados["data_available"])
        self.assertIsNone(metadados["last_successful_load_at"])
        self.assertTrue(metadados["is_stale"])
        self.assertIn("PUBLICATION_TIMESTAMP_MISSING", metadados["stale_reason"])

    def test_municipio_sem_publicacao_e_taxas_indisponiveis(self) -> None:
        metadados = self._metadados(publicacoes=[], falhas=[{
            "codigo_municipio_tce": "010",
            "competencia": "2025-01",
            "finalizado_em": self.agora,
        }])
        resposta = tce_observability.tornar_percentuais_indisponiveis({
            **metadados,
            "valores": {"total_centavos": 0, "participacao_percentual": 0.0},
        })

        self.assertFalse(resposta["data_available"])
        self.assertIsNone(resposta["data_as_of"])
        self.assertIsNone(resposta["last_successful_load_at"])
        self.assertTrue(resposta["is_stale"])
        self.assertEqual(resposta["valores"]["total_centavos"], 0)
        self.assertIsNone(resposta["valores"]["participacao_percentual"])

    @patch("app.pipeline.tce_observability.portes_historicos.raizes_classificadas_por_competencia")
    def test_periodo_parcial_origem_desconhecida_e_porte_historico(self, portes) -> None:
        portes.return_value = {"2025-01": {"11111111"}}
        registros = [
            {
                "cnpj_fornecedor": "11111111000111",
                "data_empenho": "2025-01-10",
                "origem_fornecedor": "NO_MUNICIPIO_COMPRADOR",
                "valor_liquido_centavos": 1000,
            },
            {
                "cnpj_fornecedor": "22222222000122",
                "data_empenho": "2025-01-20",
                "origem_fornecedor": "ORIGEM_NAO_IDENTIFICADA",
                "valor_liquido_centavos": 500,
            },
        ]
        metadados = self._metadados(
            data_inicial=date(2025, 1, 2),
            data_final=date(2025, 1, 30),
            competencias_solicitadas=["2025-01", "2025-02"],
            registros=registros,
        )

        self.assertTrue(metadados["quality"]["partial_period"])
        self.assertEqual(metadados["quality"]["missing_competencies"], ["2025-02"])
        self.assertEqual(metadados["quality"]["municipalities_with_data"], 1)
        self.assertEqual(metadados["quality"]["municipalities_in_scope"], 1)
        self.assertEqual(metadados["quality"]["unknown_origin"]["expenses"], 1)
        self.assertEqual(metadados["quality"]["unknown_origin"]["value_centavos"], 500)
        self.assertEqual(metadados["quality"]["unknown_origin"]["percent"], 50.0)
        self.assertEqual(
            metadados["quality"]["historical_size_classification_coverage"],
            {
                "classified_expenses": 1,
                "expenses_in_scope": 2,
                "classification_unit": "CNPJ_BASICO",
                "reference": "EXACT_MONTHLY_COMPETENCE",
                "percent": 50.0,
            },
        )
        self.assertTrue(
            all(
                "documento" not in alerta["message"].lower()
                and "11111111" not in alerta["message"]
                for alerta in metadados["quality"]["alerts"]
            )
        )


class ConsultaAnaliticaSomenteBancoTest(unittest.TestCase):
    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    @patch("app.pipeline.analisys.fornecedores.obter_fornecedores_com_cache")
    @patch("app.pipeline.analisys.database.listar_fornecedores_cache", return_value={})
    @patch("app.pipeline.analisys.database.localizar_municipio_tce", return_value={
        "codigo_municipio_tce": "010",
        "nome": "Amontada",
        "uf": "CE",
    })
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_empenhos_liquidos_publicados", return_value=[])
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_falhas_competencias", return_value=[])
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_publicacoes_competencias", return_value={})
    def test_sem_carga_retorna_zero_monetario_taxas_nulas_sem_ingestao(
        self,
        publicacoes,
        falhas,
        empenhos,
        municipio,
        cache_fornecedores,
        obter_fornecedores,
        ingerir,
    ) -> None:
        resposta = analisys.consultar_overview_tce(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
        )

        self.assertFalse(resposta["data_available"])
        self.assertFalse(resposta["is_stale"])
        self.assertEqual(resposta["kpis"]["total_compras_ME_centavos"], 0)
        self.assertIsNone(resposta["kpis"]["percentual_participacao_me"])
        publicacoes.assert_called_once()
        falhas.assert_called_once()
        empenhos.assert_not_called()
        municipio.assert_called_once()
        cache_fornecedores.assert_not_called()
        obter_fornecedores.assert_not_called()
        ingerir.assert_not_called()

    @patch("app.pipeline.analisys.tce_despesas_pipeline.executar_ingestao_competencia_tce")
    @patch("app.pipeline.analisys.database.localizar_municipio_tce", return_value={
        "codigo_municipio_tce": "010",
        "nome": "Amontada",
        "uf": "CE",
    })
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_empenhos_liquidos_publicados")
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_falhas_competencias")
    @patch("app.pipeline.analisys.tce_despesas_persistence.listar_publicacoes_competencias")
    def test_falha_recente_mantem_o_snapshot_publicado_anterior(
        self,
        listar_publicacoes,
        listar_falhas,
        listar_empenhos,
        _municipio,
        ingerir,
    ) -> None:
        publicado_em = datetime.now(timezone.utc) - timedelta(minutes=5)
        listar_publicacoes.return_value = {"2025-01": publicado_em}
        listar_falhas.return_value = [{
            "codigo_municipio_tce": "010",
            "competencia": "2025-01",
            "finalizado_em": publicado_em + timedelta(minutes=1),
        }]
        listar_empenhos.return_value = [{
            "codigo_municipio_tce": "010",
            "data_empenho": "2025-01-15",
            "valor_empenhado_centavos": 12500,
            "valor_anulado_centavos": 0,
            "valor_liquido_centavos": 12500,
            "documento_fornecedor": None,
            "cnpj_fornecedor": None,
            "tipo_documento_fornecedor": "NAO_IDENTIFICADO",
        }]

        base, metadados = analisys.montar_base_analitica_tce_somente_banco(
            "2025-01-01",
            "2025-01-31",
            codigo_municipio="010",
        )

        self.assertEqual(len(base), 1)
        self.assertTrue(metadados["data_available"])
        self.assertTrue(metadados["is_stale"])
        self.assertIn("LATEST_LOAD_FAILED", metadados["stale_reason"])
        self.assertEqual(metadados["data_as_of"], "2025-01-31")
        _municipio.assert_called_once()
        ingerir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
