from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app import main
from app.pipeline import comparisons, kpis


def _municipio(codigo: str, ibge: str, nome: str) -> dict[str, str]:
    return {
        "codigo_municipio_tce": codigo,
        "codigo_municipio_ibge": ibge,
        "nome": nome,
        "uf": "CE",
    }


def _overview(valor: int) -> dict[str, object]:
    return {
        "last_successful_load_at": "2025-02-05T12:30:00+00:00",
        "data_available": True,
        "is_stale": False,
        "stale_reason": [],
        "quality": {"coverage": "COMPLETE"},
        "competencias_ausentes": [],
        "competencias_solicitadas": ["2025-01"],
        "competencias_publicadas": ["2025-01"],
        "kpis": {
            "total_compras_centavos": valor,
            "total_compras_ME_centavos": valor,
            "percentual_participacao_me": 100.0,
        },
        "participacao_por_porte_empresarial": [
            {"porte": "ME", "valor_centavos": valor, "percentual": 100.0}
        ],
        "elementos_despesa": [
            {"codigo": "30", "nome": "Material de consumo", "valor_final_centavos": valor}
        ],
        "evolucao_compras_consideradas": [
            {
                "periodo": "2025-01",
                "compras_consideradas_centavos": valor,
                "microempresas_centavos": valor,
            }
        ],
        "destino_recursos": [
            {"destino": "NO_MUNICIPIO_COMPRADOR", "valor_centavos": valor, "percentual": 100.0}
        ],
    }


class ComparacaoTceTest(unittest.TestCase):
    def test_aplica_filtros_independentes_do_lado(self) -> None:
        base = pd.DataFrame([
            {
                "porte_fornecedor": "ME",
                "origem_geografica": kpis.ORIGEM_FORNECEDOR_LOCAL,
                "natureza_despesa_codigo": "30",
            },
            {
                "porte_fornecedor": "DEMAIS",
                "origem_geografica": kpis.ORIGEM_FORNECEDOR_FORA_CE,
                "natureza_despesa_codigo": "39",
            },
        ])
        resultado = comparisons._aplicar_filtros(
            base,
            {
                "company_sizes": ["OTHER"],
                "supplier_origins": ["FORA_DO_CEARA"],
                "expense_element_codes": ["39"],
            },
        )
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["porte_fornecedor"], "DEMAIS")

    @patch("app.pipeline.comparisons._montar_overview_local")
    @patch("app.pipeline.comparisons.database.localizar_municipio_tce")
    def test_compara_de_dois_a_quatro_municipios_por_codigo_tce(
        self, localizar_municipio, montar_overview,
    ) -> None:
        municipios = {
            "004": _municipio("004", "2300200", "Acarau"),
            "010": _municipio("010", "2304400", "Fortaleza"),
            "157": _municipio("157", "2312908", "Sobral"),
            "057": _municipio("057", "2300754", "Amontada"),
        }
        localizar_municipio.side_effect = municipios.__getitem__
        valores = {"2300200": 10000, "2304400": 8000, "2312908": 12000, "2300754": 6000}
        montar_overview.side_effect = lambda filtros, lado: _overview(
            valores[filtros["municipality_ibge_code"]]
        )

        resultado = comparisons.consultar_comparacao_tce(
            codigo_municipio_tce=["004", "010", "157", "057"],
            data_inicial="2025-01-01",
            data_final="2025-01-31",
        )

        self.assertEqual(resultado["medida"], "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA")
        self.assertEqual(
            resultado["last_successful_load_at"],
            "2025-02-05T12:30:00+00:00",
        )
        self.assertEqual(resultado["parametros"]["codigo_municipio_tce"], ["004", "010", "157", "057"])
        self.assertEqual([item["codigo_municipio_tce"] for item in resultado["municipios"]], ["004", "010", "157", "057"])
        linha_total = next(item for item in resultado["comparacao"]["kpis"] if item["indicador"] == "total_compras_centavos")
        self.assertEqual([item["valor"] for item in linha_total["valores"]], [10000, 8000, 12000, 6000])
        self.assertEqual(len(resultado["comparacao"]["participacao_por_porte_empresarial"]), 1)
        self.assertEqual(montar_overview.call_count, 4)

    def test_rejeita_quantidade_formato_duplicacao_e_periodo_invalidos(self) -> None:
        casos = [
            (["004"], "INVALID_MUNICIPALITY_COUNT"),
            (["004", "010", "157", "057", "162"], "INVALID_MUNICIPALITY_COUNT"),
            (["04", "010"], "INVALID_MUNICIPALITY_CODE"),
            (["004", "004"], "DUPLICATE_MUNICIPALITY_CODE"),
        ]
        for codigos, codigo_erro in casos:
            with self.subTest(codigos=codigos):
                with self.assertRaises(comparisons.ComparisonRequestError) as contexto:
                    comparisons.consultar_comparacao_tce(
                        codigo_municipio_tce=codigos,
                        data_inicial="2025-01-01",
                        data_final="2025-01-31",
                    )
                self.assertEqual(contexto.exception.code, codigo_erro)

        with self.assertRaises(comparisons.ComparisonRequestError) as contexto:
            comparisons.consultar_comparacao_tce(
                codigo_municipio_tce=["004", "010"],
                data_inicial="2025-02-30",
                data_final="2025-01-31",
            )
        self.assertEqual(contexto.exception.code, "INVALID_DATE")

    @patch("app.pipeline.comparisons.database.localizar_municipio_tce", return_value=None)
    def test_retorna_404_quando_codigo_nao_existe(self, localizar_municipio) -> None:
        with self.assertRaises(comparisons.ComparisonRequestError) as contexto:
            comparisons.consultar_comparacao_tce(
                codigo_municipio_tce=["004", "010"],
                data_inicial="2025-01-01",
                data_final="2025-01-31",
            )
        self.assertEqual(contexto.exception.code, "MUNICIPALITY_NOT_FOUND")
        self.assertEqual(contexto.exception.status_code, 404)
        localizar_municipio.assert_called_once_with("004")

    def test_endpoint_retorna_envelope_de_erro_e_request_id(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/comparisons",
            "headers": [(b"x-request-id", b"req_teste_comparacao")],
        })
        resposta = main.tce_comparisons(
            ["004", "010"],
            "2025-12-31",
            "2025-01-01",
            request,
            Response(),
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.headers["X-Request-ID"], "req_teste_comparacao")
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_DATE_RANGE")
        self.assertEqual(corpo["error"]["request_id"], "req_teste_comparacao")

    def test_validacao_de_query_usa_envelope_padronizado(self) -> None:
        request = Request({
            "type": "http",
            "method": "GET",
            "path": "/pipeline/tce/comparisons",
            "headers": [],
        })
        error = RequestValidationError([{
            "type": "missing",
            "loc": ("query", "codigo_municipio_tce"),
            "msg": "Field required",
            "input": None,
        }])
        resposta = asyncio.run(main.validation_exception_handler(request, error))
        self.assertEqual(resposta.status_code, 422)
        corpo = json.loads(resposta.body)
        self.assertEqual(corpo["error"]["code"], "INVALID_REQUEST")
        self.assertTrue(corpo["error"]["request_id"].startswith("req_"))


if __name__ == "__main__":
    unittest.main()
