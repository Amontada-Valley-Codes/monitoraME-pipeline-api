from __future__ import annotations

import unittest

import numpy as np

from scripts.benchmark_nlp_embeddings import _grupos_de_partes, _max_scores_por_grupo


class BenchmarkProfilePartsTests(unittest.TestCase):
    def test_groups_all_cnaes_and_keeps_primary_separate(self) -> None:
        textos, todos, principais = _grupos_de_partes([
            {
                "profile_text": "perfil completo",
                "profile_parts": [
                    {"text": "cnae principal", "is_primary": True},
                    {"text": "cnae secundario", "is_primary": False},
                ],
            },
            {"profile_text": "perfil legado"},
        ])

        self.assertEqual(textos, ["cnae principal", "cnae secundario", "perfil legado"])
        self.assertEqual(todos, [[0, 1], [2]])
        self.assertEqual(principais, [[0], [2]])

    def test_max_scores_combines_all_parts_per_opportunity(self) -> None:
        scores = np.asarray([
            [0.1, 0.2],
            [0.8, 0.3],
            [0.5, 0.9],
        ])

        combined = _max_scores_por_grupo(scores, [[0, 1], [2]])

        self.assertEqual(combined, [[0.8, 0.3], [0.5, 0.9]])


if __name__ == "__main__":
    unittest.main()
