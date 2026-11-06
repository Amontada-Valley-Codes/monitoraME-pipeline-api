from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

import pandas as pd

from app.pipeline import analisys


class SerializacaoJsonTest(unittest.TestCase):
    def test_dataframe_para_registros_converte_nulos_e_scalars_do_pandas(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "valor_int": pd.Series([1], dtype="Int64").iloc[0],
                    "valor_nulo": pd.NA,
                    "valor_nan": float("nan"),
                    "data": pd.Timestamp("2025-01-15"),
                }
            ]
        )

        registros = analisys.dataframe_para_registros(df)

        self.assertEqual(
            registros,
            [
                {
                    "valor_int": 1,
                    "valor_nulo": None,
                    "valor_nan": None,
                    "data": "2025-01-15T00:00:00",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
