from __future__ import annotations

import unittest

from scripts.benchmark_nlp_retrieval import _metricas


class RetrievalBenchmarkMetricsTests(unittest.TestCase):
    def test_ids_com_origem_correspondem_aos_julgamentos_da_amostra(self) -> None:
        judgment_id = "PNCP|75687681000107-1-000113/2026"
        metrics = _metricas(
            [judgment_id],
            {judgment_id: 2},
            [1],
        )

        self.assertEqual(metrics["relevant"], 1)
        self.assertEqual(metrics["precision@1"], 1.0)
        self.assertEqual(metrics["recall@1"], 1.0)

    def test_id_sem_origem_nao_e_contado_como_hit(self) -> None:
        judgment_id = "PNCP|75687681000107-1-000113/2026"
        metrics = _metricas(
            ["75687681000107-1-000113/2026"],
            {judgment_id: 2},
            [1],
        )

        self.assertEqual(metrics["precision@1"], 0.0)
        self.assertEqual(metrics["recall@1"], 0.0)


if __name__ == "__main__":
    unittest.main()
