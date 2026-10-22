from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/monitorame_test",
    "LOG_DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
    "TCE_CE_BASE_URL": "https://api-dados-abertos.tce.ce.gov.br/sim/",
    "IBGE_LOCALIDADES_BASE_URL": "https://servicodados.ibge.gov.br/api/v1/localidades",
    "OPENCNPJ_BASE_URL": "https://api.opencnpj.org",
    "UF_PADRAO": "CE",
    "CODIGO_MUNICIPIO_TCE_PADRAO": "010",
}


class ConfigTest(unittest.TestCase):
    def _reload_config_with_env(self, env: dict[str, str]):
        module_name = "app.core.config"
        original_config = sys.modules.get(module_name)
        sys.modules.pop(module_name, None)

        isolated_env = {"PYTHON_DOTENV_DISABLED": "1", **env}

        try:
            with patch.dict(os.environ, isolated_env, clear=True):
                return importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)
            if original_config is not None:
                sys.modules[module_name] = original_config

    def test_variavel_obrigatoria_ausente_falha_sem_default(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("TCE_CE_BASE_URL")

        with self.assertRaisesRegex(RuntimeError, "TCE_CE_BASE_URL"):
            self._reload_config_with_env(env)

    def test_database_url_obrigatoria(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("DATABASE_URL")

        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            self._reload_config_with_env(env)

    def test_log_database_url_obrigatoria(self) -> None:
        env = REQUIRED_ENV.copy()
        env.pop("LOG_DATABASE_URL")

        with self.assertRaisesRegex(RuntimeError, "LOG_DATABASE_URL"):
            self._reload_config_with_env(env)

    def test_variaveis_configuradas_sao_carregadas(self) -> None:
        config = self._reload_config_with_env(REQUIRED_ENV)

        self.assertEqual(config.DATABASE_URL, "postgresql://postgres:postgres@localhost:5432/monitorame_test")
        self.assertEqual(
            config.LOG_DATABASE_URL,
            "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
        )
        self.assertEqual(config.TCE_CE_BASE_URL, "https://api-dados-abertos.tce.ce.gov.br/sim")
        self.assertEqual(len(config.NATUREZAS_DESPESA_CONSIDERADAS), 7)
        self.assertEqual(
            config.CODIGOS_NATUREZAS_DESPESA_CONSIDERADAS,
            ("39", "51", "30", "52", "32", "35", "40"),
        )
        self.assertEqual(config.MEDIDA_FINANCEIRA_PAGAMENTOS_TCE, "valor_nota_pagamento")
        self.assertEqual(config.ESTADO_NAO_ESTORNADO_PAGAMENTOS_TCE, 0)
        self.assertEqual(config.PORTE_ME_PUBLICO_CODIGO_TCE, "01")
        self.assertTrue(config.PORTE_ME_PUBLICO_INCLUI_MEI)
        self.assertEqual(
            config.CATEGORIAS_RECONCILIACAO_PAGAMENTOS_TCE,
            ("ME", "EPP", "DEMAIS", "NAO_IDENTIFICADO", "CPF", "DOCUMENTO_INVALIDO"),
        )
        self.assertEqual(config.VERSAO_METODOLOGICA_EMPENHOS_TCE, "tce-empenhos-v1")
        self.assertEqual(config.VERSAO_METODOLOGICA_PAGAMENTOS_TCE, "tce-pagamentos-v1")
        self.assertEqual(config.TCE_RETRY_MAX_ATTEMPTS, 5)
        self.assertEqual(config.TCE_RETRY_INITIAL_DELAY_SECONDS, 2)
        self.assertEqual(config.TCE_RETRY_MAX_DELAY_SECONDS, 60)
        self.assertEqual(config.TCE_RETRY_BACKOFF_MULTIPLIER, 2)
        self.assertEqual(config.TCE_RETRY_JITTER, "full")
        self.assertTrue(config.TCE_RETRY_RESPECT_RETRY_AFTER)
        self.assertEqual(
            config.TCE_RETRYABLE_STATUS_CODES,
            (408, 429, 500, 502, 503, 504),
        )

    def test_politica_retry_pode_ser_configurada_por_ambiente(self) -> None:
        env = {
            **REQUIRED_ENV,
            "TCE_RETRY_MAX_ATTEMPTS": "7",
            "TCE_RETRY_INITIAL_DELAY_SECONDS": "0.5",
            "TCE_RETRY_MAX_DELAY_SECONDS": "30",
            "TCE_RETRY_BACKOFF_MULTIPLIER": "3",
            "TCE_RETRY_JITTER": "full",
            "TCE_RETRY_RESPECT_RETRY_AFTER": "false",
            "TCE_RETRYABLE_STATUS_CODES": "408, 429, 503",
        }

        config = self._reload_config_with_env(env)

        self.assertEqual(config.TCE_RETRY_MAX_ATTEMPTS, 7)
        self.assertEqual(config.TCE_RETRY_INITIAL_DELAY_SECONDS, 0.5)
        self.assertEqual(config.TCE_RETRY_MAX_DELAY_SECONDS, 30)
        self.assertEqual(config.TCE_RETRY_BACKOFF_MULTIPLIER, 3)
        self.assertEqual(config.TCE_RETRY_JITTER, "full")
        self.assertFalse(config.TCE_RETRY_RESPECT_RETRY_AFTER)
        self.assertEqual(config.TCE_RETRYABLE_STATUS_CODES, (408, 429, 503))

    def test_politica_retry_rejeita_configuracao_invalida(self) -> None:
        invalidos = {
            "TCE_RETRY_MAX_ATTEMPTS": "0",
            "TCE_RETRY_INITIAL_DELAY_SECONDS": "-1",
            "TCE_RETRY_MAX_DELAY_SECONDS": "invalido",
            "TCE_RETRY_BACKOFF_MULTIPLIER": "0.5",
            "TCE_RETRY_JITTER": "",
            "TCE_RETRY_RESPECT_RETRY_AFTER": "talvez",
            "TCE_RETRYABLE_STATUS_CODES": "99,600",
        }
        for chave, valor in invalidos.items():
            with self.subTest(chave=chave), self.assertRaisesRegex(RuntimeError, chave):
                self._reload_config_with_env({**REQUIRED_ENV, chave: valor})



if __name__ == "__main__":
    unittest.main()
