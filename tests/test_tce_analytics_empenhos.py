from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.pipeline import analisys
from app.pipeline.cleaners import opencnpj as opencnpj_cleaning
from app.pipeline.persistence.tce_analytics import SnapshotDespesasTce, SnapshotPagamentosTce


def _empenho(
    numero: str,
    data_empenho: str,
    natureza: str,
    valor_empenhado: int,
    valor_anulado: int,
    cnpj: str,
    nome: str,
) -> dict[str, object]:
    return {
        "codigo_municipio_tce": "010",
        "chave_empenho": f"010|202500|17|01|{data_empenho}|{numero}",
        "exercicio_orcamento": 202500,
        "codigo_orgao": "17",
        "codigo_unidade_orcamentaria": "01",
        "data_empenho": data_empenho,
        "numero_empenho": numero,
        "codigo_natureza_despesa": f"3390{natureza}00",
        "codigo_elemento_despesa": natureza,
        "valor_empenhado_centavos": valor_empenhado,
        "valor_anulado_centavos": valor_anulado,
        "valor_liquido_centavos": valor_empenhado - valor_anulado,
        "tipo_documento_fornecedor": "1",
        "documento_fornecedor": cnpj,
        "cnpj_fornecedor": cnpj,
        "cpf_fornecedor": None,
        "nome_fornecedor": nome,
        "municipio_fornecedor_informado": None,
        "uf_fornecedor_informada": None,
        "estado_empenho": "OR",
    }


FORNECEDORES = [
    {
        "cnpj": "11444777000161",
        "razao_social": "Comercio Local LTDA",
        "porte": "Microempresa (ME)",
        "opencnpj": {
            "porte_empresa": "Microempresa (ME)",
            "municipio": "AMONTADA",
            "uf": "CE",
            "cnae_principal": "4711302",
            "simples_mei": {"opcao_mei": "N"},
        },
        "opencnpj_status": "ok",
    },
    {
        "cnpj": "98765432000111",
        "razao_social": "Fornecedor Fortaleza SA",
        "porte": "Empresa de Pequeno Porte (EPP)",
        "opencnpj": {
            "porte_empresa": "Empresa de Pequeno Porte (EPP)",
            "municipio": "FORTALEZA",
            "uf": "CE",
            "cnae_principal": "6201501",
        },
        "opencnpj_status": "ok",
    },
]
FORNECEDORES_LIMPOS = opencnpj_cleaning.limpar_fornecedores(FORNECEDORES)


class BaseAnaliticaEmpenhosTest(unittest.TestCase):
    @patch("app.pipeline.analisys.tce_analytics.carregar_snapshot_despesas")
    def test_ausencia_local_nao_dispara_ingestao_e_retorna_erro_de_dominio(
        self,
        carregar_snapshot,
    ) -> None:
        carregar_snapshot.return_value = SnapshotDespesasTce(
            publicacoes_por_competencia={},
            falhas=[],
            registros=[],
            fornecedores_cache={},
            municipio_comprador=None,
        )

        with self.assertRaises(analisys.CompetenciasTceIndisponiveisError):
            analisys.montar_base_analitica_tce(
                "2025-01-01",
                "2025-01-31",
                codigo_municipio="010",
            )

    @patch("app.pipeline.analisys.tce_analytics.carregar_snapshot_despesas")
    def test_calcula_sobre_snapshot_local_sem_ingestao(self, carregar_snapshot) -> None:
        registros = [
            _empenho(
                "31010002",
                "2025-01-31",
                "30",
                1_000_000,
                100_000,
                "11444777000161",
                "Comercio Local LTDA",
            ),
            _empenho(
                "28020001",
                "2025-02-28",
                "39",
                2_000_000,
                500_000,
                "98765432000111",
                "Fornecedor Fortaleza SA",
            ),
        ]
        fornecedores_cache = {
            str(item["cnpj"]): {"dados_normalizados": item}
            for item in FORNECEDORES_LIMPOS.to_dict(orient="records")
        }
        carregar_snapshot.return_value = SnapshotDespesasTce(
            publicacoes_por_competencia={
                "2025-01": datetime(2025, 2, 1, tzinfo=timezone.utc),
                "2025-02": datetime(2025, 3, 1, tzinfo=timezone.utc),
            },
            falhas=[],
            registros=registros,
            fornecedores_cache=fornecedores_cache,
            municipio_comprador="Amontada",
        )

        base, metadados = analisys.montar_base_analitica_tce(
            data_inicial="2025-01-15",
            data_final="2025-02-28",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )

        carregar_snapshot.assert_called_once()
        self.assertEqual(metadados["competencias_ausentes"], [])
        self.assertEqual(metadados["competencias_atualizadas"], [])
        self.assertEqual(metadados["competencias_reutilizadas"], ["2025-01", "2025-02"])
        self.assertEqual(metadados["competencias_desatualizadas"], [])
        self.assertEqual(metadados["base_analitica"]["nome"], "despesas_tce")
        self.assertEqual(metadados["base_analitica"]["base_calculo"], "DESPESA_FINAL")
        self.assertEqual(metadados["totais"], {"despesas": 2})

        janeiro = base.loc[base["numero_empenho"] == "31010002"].iloc[0]
        self.assertEqual(janeiro["base_calculo"], "empenhos_tce")
        self.assertEqual(janeiro["valor_empenhado"], 10_000.0)
        self.assertEqual(janeiro["valor_anulado"], 1_000.0)
        self.assertEqual(janeiro["valor"], 9_000.0)
        self.assertEqual(janeiro["porte_fornecedor"], "ME")
        self.assertFalse(janeiro["optante_mei"])
        self.assertTrue(janeiro["mei_discriminado"])
        self.assertEqual(janeiro["fonte_porte"], "RECEITA_FEDERAL_VIA_OPENCNPJ")
        self.assertEqual(janeiro["procedencia_porte"], "RETRATO_ATUAL")
        self.assertIsNotNone(janeiro["observado_em"])
        self.assertEqual(janeiro["origem_geografica"], "Sediado no município comprador")

    @patch("app.pipeline.analisys.tce_analytics.carregar_snapshot_despesas")
    def test_snapshot_parcial_sinaliza_competencia_ausente(self, carregar_snapshot) -> None:
        carregar_snapshot.return_value = SnapshotDespesasTce(
            publicacoes_por_competencia={
                "2025-01": datetime(2025, 2, 1, tzinfo=timezone.utc),
            },
            falhas=[{"competencia": "2025-02", "erro": "fevereiro indisponível"}],
            registros=[
            _empenho("J1", "2025-01-20", "30", 10_000, 0, "", "Janeiro"),
            ],
            fornecedores_cache={},
            municipio_comprador="Amontada",
        )

        base, metadados = analisys.montar_base_analitica_tce(
            data_inicial="2025-01-01",
            data_final="2025-02-28",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )

        self.assertEqual(base["numero_empenho"].tolist(), ["J1"])
        self.assertEqual(metadados["competencias_publicadas"], ["2025-01"])
        self.assertEqual(metadados["competencias_ausentes"], ["2025-02"])
        self.assertEqual(metadados["falhas_competencias"][0]["erro"], "fevereiro indisponível")
        self.assertFalse(metadados["falhas_competencias"][0]["usou_versao_publicada"])


class IndicadoresEmpenhosTest(unittest.TestCase):
    def setUp(self) -> None:
        self.base = None

    @patch("app.pipeline.analisys.montar_base_analitica_pagamentos_tce_somente_banco")
    def test_indicadores_e_porte_mensal_usam_pagamentos(
        self,
        montar_base_pagamentos,
    ) -> None:
        montar_base_pagamentos.return_value = (
            pd.DataFrame([
                {
                    "ano_mes": "2025-01", "data_referencia": "2025-01-10",
                    "valor": 8_000.0, "cnpj_fornecedor": "11444777000161",
                    "nome_fornecedor": "Comercio Local LTDA", "porte_fornecedor": "ME",
                    "fornecedor_e_me": True, "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "30", "natureza_despesa": "Material de consumo",
                    "origem_geografica": "Sediado no municÃ­pio comprador",
                    "municipio_sede_fornecedor": "AMONTADA", "uf_sede_fornecedor": "CE",
                },
                {
                    "ano_mes": "2025-01", "data_referencia": "2025-01-20",
                    "valor": 15_000.0, "cnpj_fornecedor": "98765432000111",
                    "nome_fornecedor": "Fornecedor Fortaleza SA", "porte_fornecedor": "EPP",
                    "fornecedor_e_me": False, "fornecedor_e_mpe": True,
                    "natureza_despesa_codigo": "39", "natureza_despesa": "Outros servicos",
                    "origem_geografica": "Outro municÃ­pio do CearÃ¡",
                    "municipio_sede_fornecedor": "FORTALEZA", "uf_sede_fornecedor": "CE",
                },
            ]),
            {"data_available": True, "totais": {"despesas": 2, "pagamentos": 2}},
        )

        indicadores = analisys.consultar_tce_indicadores_analiticos(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            municipio_comprador="Amontada",
            throttle_fornecedores=0,
        )
        portes = analisys.consultar_kpi_tce_portes_por_mes(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )

        resumo = indicadores["indicadores"]["resumo_geral"][0]
        self.assertEqual(resumo["valor_total"], 23_000.0)
        self.assertEqual(resumo["valor_me"], 8_000.0)
        self.assertEqual(resumo["valor_mpe"], 23_000.0)
        self.assertTrue(resumo["mei_discriminado"])
        self.assertEqual(resumo["estado_mpe"], "DISPONIVEL")
        self.assertIsNone(resumo["motivo_mpe"])
        self.assertEqual(portes["totais"]["pagamentos"], 2)
        linha_me = next(linha for linha in portes["dados"] if linha["porte"] == "ME")
        self.assertEqual(linha_me["total_empenhos"], 2)
        self.assertEqual(linha_me["quantidade_empenhos"], 1)
        self.assertEqual(linha_me["valor_porte"], 8_000.0)
        self.assertNotIn("total_contratos", linha_me)


class ResolverMunicipioTceTest(unittest.TestCase):
    @patch("app.pipeline.analisys._consultar_overview_tce_estado")
    def test_overview_sem_municipio_consolida_ceara_somente_com_banco_local(
        self, consultar_estado,
    ) -> None:
        consultar_estado.return_value = {
            "parametros": {"codigo_municipio": None},
            "escopo_territorial": {"tipo": "ESTADO"},
            "origem_dados": "BANCO_LOCAL",
            "consultas_externas": False,
            "kpis": {"total_compras_ME_centavos": 0},
        }

        resultado = analisys.consultar_overview_tce(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio=None,
        )

        self.assertEqual(resultado["parametros"]["codigo_municipio"], None)
        self.assertEqual(resultado["escopo_territorial"]["tipo"], "ESTADO")
        self.assertEqual(resultado["origem_dados"], "BANCO_LOCAL")
        self.assertFalse(resultado["consultas_externas"])
        self.assertEqual(resultado["kpis"]["total_compras_ME_centavos"], 0)
        consultar_estado.assert_called_once_with("2025-01-01", "2025-01-31")

    @patch("app.pipeline.analisys.montar_base_analitica_pagamentos_tce_somente_banco")
    def test_overview_usa_pagamentos_e_restringe_numerador_a_me(
        self,
        montar_base,
    ) -> None:
        montar_base.return_value = (
            pd.DataFrame(
                [
                    {
                        "ano_mes": "2025-01",
                        "valor": 80.0,
                        "porte_fornecedor": "ME",
                        "origem_geografica": "Sediado no município comprador",
                        "natureza_despesa_codigo": "30",
                    },
                    {
                        "ano_mes": "2025-01",
                        "valor": 20.0,
                        "porte_fornecedor": "MEI",
                        "origem_geografica": "Outro município do Ceará",
                        "natureza_despesa_codigo": "39",
                    },
                ]
            ),
            {
                "fonte": "TCE-CE/SIM",
                "competencias_publicadas": ["2025-01"],
                "competencias_ausentes": [],
                "data_available": True,
                "totais": {"pagamentos": 2},
            },
        )

        resultado = analisys.consultar_overview_tce(
            data_inicial="2025-01-01",
            data_final="2025-01-31",
            codigo_municipio="010",
            throttle_fornecedores=0,
        )

        montar_base.assert_called_once_with(
            "2025-01-01",
            "2025-01-31",
            codigo_municipio="010",
            exercicio_orcamento=2025,
        )
        self.assertEqual(resultado["competencias_publicadas"], ["2025-01"])
        self.assertEqual(resultado["escopo"]["portes_considerados"], ["ME"])
        self.assertEqual(
            resultado["escopo"]["portes_no_denominador_participacao"],
            ["ME", "MEI", "EPP", "DEMAIS"],
        )
        self.assertEqual(resultado["unidade_monetaria"], "CENTAVOS")
        self.assertEqual(resultado["medida"], "VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA")
        self.assertEqual(resultado["kpis"]["percentual_participacao_me"], 80.0)
        self.assertEqual(
            resultado["kpis"]["total_compras_ME_centavos"],
            8_000,
        )

    @patch("app.pipeline.analisys.tce_analytics.carregar_snapshot_pagamentos_municipais")
    def test_base_overview_usa_valor_pago_e_exige_snapshots_completos(
        self, carregar_snapshot,
    ):
        carregar_snapshot.return_value = SnapshotPagamentosTce(
            registros=[{
                "ano_mes": "2026-02", "data_referencia": "2026-02-02",
                "valor_centavos": 1055, "natureza_despesa_codigo": "30",
                "cnpj_fornecedor": "02530104000133",
                "documento_fornecedor": "02530104000133",
                "tipo_documento_fornecedor": "1", "nome_fornecedor": "Fornecedor",
                "codigo_municipio_tce": "004",
            }],
            competencias_ausentes=[],
            competencias_publicadas=["2026-02"],
            fornecedores_cache={},
            municipio_comprador="Acarau",
        )
        base, metadata = analisys.montar_base_analitica_pagamentos_tce_somente_banco(
            "2026-02-01", "2026-02-28", codigo_municipio="004",
        )
        self.assertEqual(str(base.iloc[0]["valor"]), "10.55")
        self.assertEqual(base.iloc[0]["base_calculo"], "PAGAMENTOS_TCE")
        self.assertEqual(metadata["totais"]["pagamentos"], 1)
        carregar_snapshot.return_value = SnapshotPagamentosTce(
            registros=[],
            competencias_ausentes=[{
                "exercicio_orcamento": "202500", "competencia": "2026-02",
            }],
            competencias_publicadas=[],
            fornecedores_cache={},
            municipio_comprador="Acarau",
        )
        with self.assertRaisesRegex(
            analisys.CompetenciasTceIndisponiveisError,
            "Lotes de pagamentos publicados ausentes",
        ):
            analisys.montar_base_analitica_pagamentos_tce_somente_banco(
                "2026-02-01", "2026-02-28", codigo_municipio="004",
            )


if __name__ == "__main__":
    unittest.main()
