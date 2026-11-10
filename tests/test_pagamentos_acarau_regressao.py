from __future__ import annotations

from decimal import Decimal
import gzip
import json
from pathlib import Path
import unittest

from app.pipeline import pagamentos


FIXTURE = Path(__file__).parent / "fixtures" / "tce_pagamentos_acarau_2025_coleta_real.json.gz"
TOTAL_PAGO_CENTAVOS = 13_621_330_664
TOTAL_ME_CENTAVOS = 2_385_715_902
ME_POR_ELEMENTO_CENTAVOS = {
    "39": 1_265_940_313,
    "30": 268_580_961,
    "51": 692_012_187,
    "52": 42_501_245,
    "32": 28_493_190,
    "40": 0,
    "35": 88_188_006,
}


class RegressaoOfflineAcarauTest(unittest.TestCase):
    def test_coleta_real_reproduz_totais_e_sinaliza_classificacao_incompleta(self) -> None:
        with gzip.open(FIXTURE, "rt", encoding="utf-8") as arquivo:
            fixture = json.load(arquivo)
        resultado = pagamentos.consultar_pagamentos_prefeitura(
            codigo_municipio="004",
            exercicio=2025,
            referencia_final="2026-09",
            coleta=fixture["coleta"],
            fornecedores_cache=fixture["fornecedores_cache"],
        )

        self.assertEqual(
            resultado["controle_financeiro"]["total_centavos"],
            TOTAL_PAGO_CENTAVOS,
        )
        self.assertEqual(resultado["total_me_centavos"], TOTAL_ME_CENTAVOS)
        self.assertEqual(
            resultado["me_por_elemento_centavos"],
            ME_POR_ELEMENTO_CENTAVOS,
        )
        self.assertEqual(sum(ME_POR_ELEMENTO_CENTAVOS.values()), TOTAL_ME_CENTAVOS)
        self.assertEqual(
            Decimal(resultado["controle_financeiro"]["total_centavos"]) / Decimal(100),
            Decimal("136213306.64"),
        )
        self.assertEqual(
            Decimal(resultado["total_me_centavos"]) / Decimal(100),
            Decimal("23857159.02"),
        )
        self.assertFalse(resultado["classificacao_completa"])
        self.assertEqual(len(resultado["pendencias"]), 131)

        esperado = fixture["esperado"]
        self.assertEqual(
            esperado["total_financeiro_centavos"],
            resultado["controle_financeiro"]["total_centavos"],
        )
        self.assertEqual(esperado["total_me_centavos"], resultado["total_me_centavos"])
        self.assertEqual(
            esperado["me_por_elemento_centavos"],
            resultado["me_por_elemento_centavos"],
        )
        self.assertFalse(esperado["classificacao_completa"])
        self.assertEqual(esperado["pendencias_classificacao"], len(resultado["pendencias"]))
        self.assertIsNone(resultado["total_mpe_centavos"])
        self.assertEqual(
            resultado["criterio_mpe"]["regra"],
            "ME + EPP + MEI quando indicado pelo cadastro",
        )


if __name__ == "__main__":
    unittest.main()
