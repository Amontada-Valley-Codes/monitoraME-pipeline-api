"""Validação das competências aceitas pelo importador de portes."""
import unittest
from app.pipeline import portes_historicos as service


class PoliticaTemporalTest(unittest.TestCase):
    def test_competencia_invalida(self):
        for valor in ("2025-13", "2025-00", "0000-01", "202501"):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                service.competencia(valor)
