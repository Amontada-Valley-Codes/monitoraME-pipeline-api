from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/monitorame_test",
)
os.environ.setdefault(
    "LOG_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test",
)

from app.pipeline.persistence import tce_fornecedores


class _ResultadoVazio:
    def mappings(self) -> _ResultadoVazio:
        return self

    def all(self) -> list[object]:
        return []

    def first(self) -> None:
        return None


class _SessaoCaptura:
    def __init__(self) -> None:
        self.consultas: list[object] = []

    def execute(self, consulta):
        self.consultas.append(consulta)
        return _ResultadoVazio()


class FornecedoresPersistenceSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sessao = _SessaoCaptura()

    @contextmanager
    def _main_session(self):
        yield self.sessao

    def _patches(self):
        return (
            patch("app.pipeline.persistence.tce_fornecedores.database.init_db"),
            patch(
                "app.pipeline.persistence.tce_fornecedores.orm.main_session",
                side_effect=self._main_session,
            ),
        )

    def test_listagem_reutiliza_identidade_canonica_no_select_group_by_e_order_by(
        self,
    ) -> None:
        init_db, main_session = self._patches()
        with init_db, main_session:
            resultado = tce_fornecedores.listar_fornecedores_publicados_paginados(
                data_inicial=date(2025, 1, 1),
                data_final=date(2025, 12, 31),
                codigo_municipio_tce=None,
                portes=[],
                origens=[],
                elementos=["30"],
                busca=None,
                pagina=1,
                tamanho_pagina=50,
                ordenar_por="final_value",
                ordem="desc",
            )

        self.assertEqual(resultado, {"total": 0, "registros": []})
        consulta = self.sessao.consultas[0]
        compilada = consulta.compile(dialect=postgresql.dialect())
        sql = str(compilada)
        order_by = sql.split(" ORDER BY ", maxsplit=1)[1]

        self.assertEqual(
            sum(valor == "1" for valor in compilada.params.values()),
            1,
        )
        self.assertGreaterEqual(
            order_by.count("tce_empenhos.cnpj_fornecedor IS NOT NULL"),
            2,
        )

    def test_detalhe_reutiliza_identidade_canonica_no_select_e_group_by(self) -> None:
        init_db, main_session = self._patches()
        with init_db, main_session:
            resultado = tce_fornecedores.obter_resumo_fornecedor_publicado(
                tipo_documento="1",
                documento="35187278000102",
                data_inicial=date(2025, 1, 1),
                data_final=date(2025, 12, 31),
                codigo_municipio_tce=None,
                portes=[],
                origens=[],
                elementos=["30"],
            )

        self.assertIsNone(resultado)
        consulta = self.sessao.consultas[0]
        compilada = consulta.compile(dialect=postgresql.dialect())

        self.assertEqual(
            sum(valor == "1" for valor in compilada.params.values()),
            1,
        )


if __name__ == "__main__":
    unittest.main()
