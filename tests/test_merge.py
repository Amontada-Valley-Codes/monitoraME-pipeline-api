from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

import pandas as pd

from app.pipeline.cleaners import ibge as ibge_cleaning
from app.pipeline.cleaners import opencnpj as opencnpj_cleaning
from app.pipeline.enrichment.fornecedores import enriquecer_com_fornecedor, extrair_cnpjs_distintos
from app.pipeline.enrichment.municipios import validar_e_enriquecer_municipio
from app.pipeline.enrichment.origem_geografica import (
    NIVEL_FORA_DO_ESTADO,
    NIVEL_MESMO_MUNICIPIO,
    NIVEL_OUTRO_MUNICIPIO_CE,
    classificar_origem_geografica,
)
from app.pipeline.ingestion.ibge import buscar_municipios_uf
from app.pipeline.ingestion import fornecedores, ibge



def setUpModule() -> None:
    # A persistencia do cache nao integra estes testes de limpeza/merge.
    # Evita tentar conexoes ao banco definido no .env durante testes unitarios.
    unittest.enterModuleContext(patch("app.pipeline.ingestion.ibge.database.salvar_municipios_ibge"))
    unittest.enterModuleContext(patch("app.core.logging.log_database.registrar_log_ingestao"))
    unittest.enterModuleContext(patch("app.core.logging.log_database.registrar_log_aplicacao"))


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


def _fornecedores_df_valido() -> pd.DataFrame:
    with patch("app.pipeline.ingestion.fornecedores.buscar_opencnpj") as mock_opencnpj:
        mock_opencnpj.return_value = {
            "cnpj": "11444777000161",
            "razao_social": "Comércio Exemplo LTDA",
            "porte_empresa": "MICRO EMPRESA",
            "municipio": "AMONTADA",
            "uf": "CE",
            "cnae_principal": "6201501",
            "cnaes_secundarios": ["4751201", "9511800"],
            "simples_mei": {"opcao_mei": "N"},
        }
        fornecedor = fornecedores.coletar_fornecedor("11.444.777/0001-61")

    return opencnpj_cleaning.limpar_fornecedores([fornecedor])


def _municipios_ibge_df() -> pd.DataFrame:
    abaiara = {
        "id": 2301000,
        "nome": "Abaiara",
        "microrregiao": {
            "id": 23014,
            "nome": "Baturite",
            "mesorregiao": {
                "id": 2303,
                "nome": "Norte Cearense",
                "UF": {"id": 23, "sigla": "CE", "nome": "Ceara"},
            },
        },
    }
    with patch("app.pipeline.ingestion.ibge.requests.get") as mock_get:
        mock_get.return_value = _fake_response([abaiara])
        registros = ibge.coletar_municipios("CE")
    return ibge_cleaning.limpar_municipios(registros)


class EnriquecerComFornecedorTest(unittest.TestCase):
    def test_traz_dados_do_fornecedor_e_marca_nao_localizado(self) -> None:
        fornecedores_df = _fornecedores_df_valido()
        df = pd.DataFrame(
            [
                # formatado diferente do cnpj normalizado na base de fornecedores
                {"contrato_id": 1, "ni_fornecedor": "11.444.777/0001-61"},
                {"contrato_id": 2, "ni_fornecedor": "99.999.999/0001-00"},
            ]
        )

        resultado = enriquecer_com_fornecedor(df, fornecedores_df, coluna_cnpj="ni_fornecedor")

        encontrado = resultado[resultado["contrato_id"] == 1].iloc[0]
        self.assertEqual(encontrado["fornecedor_porte_padronizado"], "ME")
        self.assertTrue(encontrado["fornecedor_elegivel_me"])
        self.assertFalse(encontrado["fornecedor_optante_mei"])
        self.assertTrue(encontrado["fornecedor_mei_discriminado"])
        self.assertEqual(encontrado["fornecedor_fonte_porte"], "RECEITA_FEDERAL_VIA_OPENCNPJ")
        self.assertEqual(encontrado["fornecedor_procedencia_porte"], "RETRATO_ATUAL")
        self.assertIsNotNone(encontrado["fornecedor_observado_em"])

        nao_encontrado = resultado[resultado["contrato_id"] == 2].iloc[0]
        self.assertTrue(pd.isna(nao_encontrado["fornecedor_porte_padronizado"]))

    def test_enriquece_despesa_com_porte_municipio_sede_e_cnae_para_cnpj_conhecido(self) -> None:
        fornecedores_df = _fornecedores_df_valido()
        despesas = pd.DataFrame(
            [
                {
                    "despesa_id": "D1",
                    "cnpj_fornecedor": "11.444.777/0001-61",
                    "valor": 1500.0,
                }
            ]
        )

        resultado = enriquecer_com_fornecedor(
            despesas,
            fornecedores_df,
            coluna_cnpj="cnpj_fornecedor",
        )

        despesa = resultado.iloc[0]
        self.assertEqual(despesa["fornecedor_porte_padronizado"], "ME")
        self.assertEqual(despesa["fornecedor_municipio_sede"], "AMONTADA")
        self.assertEqual(despesa["fornecedor_uf_sede"], "CE")
        self.assertEqual(despesa["fornecedor_cnae_principal_codigo"], "6201501")
        self.assertTrue(pd.isna(despesa["fornecedor_cnae_principal_descricao"]))
        self.assertEqual(despesa["fornecedor_cnaes"], ["4751201", "9511800"])


class ExtrairCnpjsDistintosTest(unittest.TestCase):
    def test_deduplica_normaliza_e_descarta_documentos_invalidos(self) -> None:
        contratados = pd.Series(
            [
                "11.444.777/0001-61",
                "11444777000161",  # mesmo CNPJ do de cima, formatado diferente -> deduplica
                "111.444.777-35",  # CPF (11 digitos) -> descartado, base so cobre CNPJ
                "p3ADBbgv35B4W78xV4/xiA==",  # valor mascarado real visto no TCE -> descartado
                None,
            ]
        )
        fornecedores_adicionais = pd.Series(["98.765.432/0001-11", None])

        resultado = extrair_cnpjs_distintos(contratados, fornecedores_adicionais)

        self.assertEqual(resultado, ["11444777000161", "98765432000111"])

    def test_ignora_series_none(self) -> None:
        contratados = pd.Series(["11.444.777/0001-61"])

        resultado = extrair_cnpjs_distintos(contratados, None)

        self.assertEqual(resultado, ["11444777000161"])


class ValidarEEnriquecerMunicipioTest(unittest.TestCase):
    def test_traz_nome_uf_oficiais_e_sinaliza_divergencia(self) -> None:
        municipios = _municipios_ibge_df()
        df = pd.DataFrame(
            [
                {"id_registro": "A", "codigo_ibge": 2301000, "uf_informada": "CE"},
                {"id_registro": "B", "codigo_ibge": 2301000, "uf_informada": "SP"},
            ]
        )

        resultado = validar_e_enriquecer_municipio(
            df, municipios, coluna_codigo_municipio="codigo_ibge", coluna_uf="uf_informada"
        )

        por_id = resultado.set_index("id_registro")
        self.assertEqual(por_id.loc["A", "municipio_nome"], "Abaiara")
        self.assertEqual(por_id.loc["A", "municipio_uf"], "CE")
        self.assertTrue(por_id.loc["A", "codigo_ibge_uf_confere"])
        self.assertFalse(por_id.loc["B", "codigo_ibge_uf_confere"])


def _fake_response(payload, status_code: int = 200) -> MagicMock:
    resposta = MagicMock()
    resposta.status_code = status_code
    resposta.json.return_value = payload
    resposta.raise_for_status.return_value = None
    return resposta


UFS_BRASIL = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)


class ClassificarOrigemGeograficaTest(unittest.TestCase):
    def test_caso_1_mesmo_municipio(self) -> None:
        resultado = classificar_origem_geografica("Fortaleza", "CE", "Fortaleza")
        self.assertEqual(resultado, NIVEL_MESMO_MUNICIPIO)

    def test_caso_2_outro_municipio_ce(self) -> None:
        resultado = classificar_origem_geografica("Fortaleza", "CE", "Sobral")
        self.assertEqual(resultado, NIVEL_OUTRO_MUNICIPIO_CE)

    def test_caso_3_fora_do_estado(self) -> None:
        # Cobre TODOS os estados do Brasil, exceto o CE (26 UFs) — garante
        # que a classificacao escala para qualquer estado, nao so os
        # exemplos mais comuns (SP, RJ etc.).
        for uf in UFS_BRASIL:
            if uf == "CE":
                continue
            with self.subTest(uf=uf):
                resultado = classificar_origem_geografica("Fortaleza", uf, "Qualquer Município")
                self.assertEqual(resultado, NIVEL_FORA_DO_ESTADO)


class BuscarMunicipiosUfTest(unittest.TestCase):
    def test_retorna_nomes_ordenados_e_sem_duplicatas(self) -> None:
        payload = [
            {"nome": "Sobral"},
            {"nome": "Fortaleza"},
            {"nome": "Juazeiro do Norte"},
            {"nome": "Sobral"},  # duplicata proposital
        ]
        with patch("app.pipeline.ingestion.ibge.coletar_municipios") as coletar:
            coletar.return_value = payload
            resultado = buscar_municipios_uf("CE")

        self.assertEqual(resultado, ["Fortaleza", "Juazeiro do Norte", "Sobral"])
        coletar.assert_called_once_with("CE")

    def test_uf_minuscula_e_normalizada_na_url(self) -> None:
        with patch("app.pipeline.ingestion.ibge.coletar_municipios") as coletar:
            coletar.return_value = [{"nome": "São Paulo"}]
            buscar_municipios_uf("sp")

        coletar.assert_called_once_with("SP")

if __name__ == "__main__":
    unittest.main()
