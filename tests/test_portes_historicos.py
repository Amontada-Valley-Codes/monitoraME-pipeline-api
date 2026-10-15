"""Validacao temporal; integracao usa apenas os dados reais importados."""
import os
import unittest
from app.pipeline import portes_historicos as service


class PoliticaTemporalTest(unittest.TestCase):
    def test_intervalo_inclui_meses_ausentes_e_limita_tamanho(self):
        self.assertEqual(service.meses("2025-01", "2025-03"), ["2025-01","2025-02","2025-03"])
        with self.assertRaises(ValueError):
            service.meses("2026-10", "2025-01")
        with self.assertRaises(ValueError):
            service.meses("2000-01", "2026-10")

    def test_competencia_invalida(self):
        for valor in ("2025-13", "2025-00", "0000-01", "202501"):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                service.competencia(valor)


@unittest.skipUnless(os.getenv("RUN_PORTE_INTEGRATION") == "1", "Requer piloto real importado")
class PortesReaisTest(unittest.TestCase):
    def test_coorte_real_e_lacunas_nao_recebem_porte_futuro(self):
        from pathlib import Path
        cnpj = Path("artifacts/pen-003/coleta-ce-2025-01/cnpjs-reais.txt").read_text().splitlines()[0]
        result = service.historico(cnpj, "2024-12", "2026-10")
        items = {i["competencia"]:i for i in result["items"]}
        self.assertEqual(items["2024-12"]["status"], "SEM_PUBLICACAO")
        self.assertIsNone(items["2024-12"]["porte_observado"])
        self.assertEqual(items["2025-02"]["status"], "SEM_PUBLICACAO")
        self.assertIsNone(items["2025-02"]["porte_observado"])
        self.assertEqual(len(result["competencias_sem_publicacao"]), 21)
        self.assertFalse(result["external_requests"])
        for mes in ("2025-01", "2026-10"):
            p = items[mes]["publicacao"]
            self.assertEqual(p["cobertura"]["raizes_selecionadas"], 8366)
            self.assertEqual(len(p["artefatos"]), 11)
            self.assertFalse(p["vigencia_diaria_comprovada"])
            replay = service.historico(cnpj, versao=p["versao"])
            self.assertEqual(replay["items"][0], items[mes])

    def test_limite_temporal_exclui_publicacao_posterior(self):
        r = service.publicacoes("2025-01")
        self.assertEqual([i["competencia"] for i in r["items"]], ["2025-01"])
