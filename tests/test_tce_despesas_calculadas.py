from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_test")
os.environ.setdefault("LOG_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/monitorame_logs_test")
os.environ.setdefault("TCE_CE_BASE_URL", "https://api-dados-abertos.tce.ce.gov.br/sim")
os.environ.setdefault("IBGE_LOCALIDADES_BASE_URL", "https://servicodados.ibge.gov.br/api/v1/localidades")
os.environ.setdefault("OPENCNPJ_BASE_URL", "https://api.opencnpj.org")
os.environ.setdefault("UF_PADRAO", "CE")
os.environ.setdefault("CODIGO_MUNICIPIO_TCE_PADRAO", "010")

from app.core.models import TceDespesaCalculada, TceDespesaCalculadaRun
from app.pipeline.persistence import tce_despesas_calculadas as persistence


CHAVE_EMPENHO = "010|202500|17|01|2025-01-31|31010002"


def _base(**alteracoes):
    registro = {
        "fonte": "TCE-CE",
        "source_run_id": 10,
        "source_record_id": 100,
        "competencia_documento": "2025-01",
        "competencia_analitica": "2025-01",
        "data_referencia": "2025-01-31",
        "exercicio_orcamento": 202500,
        "codigo_municipio_tce": "010",
        "codigo_municipio_ibge": "2300754",
        "municipio": "Amontada",
        "uf": "CE",
        "codigo_orgao": "17",
        "nome_orgao": "Prefeitura Municipal",
        "codigo_unidade_orcamentaria": "01",
        "tipo_documento_fornecedor": "CNPJ",
        "documento_fornecedor": "10496308000123",
        "cnpj_fornecedor": "10496308000123",
        "cpf_fornecedor": None,
        "nome_fornecedor": "Fornecedor LTDA",
        "municipio_fornecedor": "Amontada",
        "uf_fornecedor": "CE",
        "porte_fornecedor": "ME",
        "fonte_porte": "RECEITA_FEDERAL_VIA_OPENCNPJ",
        "porte_observado_em": "2025-02-01T12:00:00+00:00",
        "natureza_despesa_codigo": "52",
        "natureza_despesa_descricao": "Equipamentos e material permanente",
        "atributos": {},
    }
    registro.update(alteracoes)
    return registro


def _empenho(**alteracoes):
    registro = _base(
        natureza="EMPENHO",
        chave_registro=CHAVE_EMPENHO,
        chave_empenho=CHAVE_EMPENHO,
        valor_centavos=90_000,
        valor_empenhado_centavos=100_000,
        valor_anulado_centavos=10_000,
        valor_liquido_centavos=90_000,
    )
    registro.update(alteracoes)
    return registro


def _anulacao(**alteracoes):
    registro = _base(
        natureza="ANULACAO_EMPENHO",
        chave_registro=f"{CHAVE_EMPENHO}|2025-02-10|10020001",
        chave_empenho=CHAVE_EMPENHO,
        source_run_id=11,
        source_record_id=101,
        competencia_documento="2025-02",
        competencia_analitica="2025-02",
        data_referencia="2025-02-10",
        valor_centavos=10_000,
    )
    registro.update(alteracoes)
    return registro


def _pagamento(**alteracoes):
    registro = _base(
        natureza="PAGAMENTO",
        chave_registro=f"{CHAVE_EMPENHO}|01|0001",
        chave_empenho=CHAVE_EMPENHO,
        source_run_id=12,
        source_record_id=102,
        competencia_documento="2025-03",
        competencia_analitica="2025-01",
        data_referencia="2025-03-15",
        valor_centavos=50_000,
    )
    registro.update(alteracoes)
    return registro


class ContratoCalculadoTest(unittest.TestCase):
    def _validar(self, registros):
        return persistence.validar_resultados_calculados(
            codigo_municipio_tce="010",
            exercicio_orcamento=202500,
            registros=registros,
        )

    def test_valida_tres_naturezas_e_preserva_competencias_distintas(self) -> None:
        resultado = self._validar([_pagamento(), _empenho(), _anulacao()])

        por_natureza = {item["natureza"]: item for item in resultado}
        self.assertEqual(set(por_natureza), {"EMPENHO", "ANULACAO_EMPENHO", "PAGAMENTO"})
        self.assertEqual(por_natureza["PAGAMENTO"]["competencia_documento"], "2025-03")
        self.assertEqual(por_natureza["PAGAMENTO"]["competencia_analitica"], "2025-01")
        self.assertEqual(len(por_natureza["EMPENHO"]["conteudo_hash"]), 64)

    def test_ordem_de_entrada_nao_altera_resultado_normalizado(self) -> None:
        primeiro = self._validar([_empenho(), _anulacao(), _pagamento()])
        segundo = self._validar([_pagamento(), _anulacao(), _empenho()])

        self.assertEqual(primeiro, segundo)

    def test_rejeita_valor_liquido_inconsistente(self) -> None:
        with self.assertRaisesRegex(
            persistence.ContratoDespesaCalculadaInvalido,
            "Valor liquido",
        ):
            self._validar([_empenho(valor_liquido_centavos=80_000)])

    def test_rejeita_filho_sem_empenho(self) -> None:
        with self.assertRaisesRegex(
            persistence.ContratoDespesaCalculadaInvalido,
            "sem empenho calculado correspondente",
        ):
            self._validar([_pagamento()])

    def test_rejeita_natureza_de_despesa_divergente_do_empenho(self) -> None:
        with self.assertRaisesRegex(
            persistence.ContratoDespesaCalculadaInvalido,
            "natureza_despesa_codigo",
        ):
            self._validar([_empenho(), _anulacao(natureza_despesa_codigo="39")])

    def test_rejeita_duplicacao_na_mesma_natureza(self) -> None:
        with self.assertRaisesRegex(
            persistence.ContratoDespesaCalculadaInvalido,
            "Registro calculado duplicado",
        ):
            self._validar([_empenho(), _empenho()])


class _SessaoFalsa:
    def __init__(self) -> None:
        self.adicionados = []
        self._proximo_id = 100

    def add(self, objeto) -> None:
        self.adicionados.append(objeto)

    def add_all(self, objetos) -> None:
        self.adicionados.extend(list(objetos))

    def flush(self) -> None:
        for objeto in self.adicionados:
            if isinstance(objeto, (TceDespesaCalculadaRun, TceDespesaCalculada)):
                if objeto.id is None:
                    objeto.id = self._proximo_id
                    self._proximo_id += 1


class PublicacaoCalculadaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.agora = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        self.sessao = _SessaoFalsa()

    @contextmanager
    def _sessao(self):
        yield self.sessao

    def _publicar(self, anterior=None, registros=None, fontes=None):
        with (
            patch.object(persistence.database, "init_db"),
            patch.object(persistence.orm, "main_session", side_effect=self._sessao),
            patch.object(persistence, "_agora", return_value=self.agora),
            patch.object(persistence, "_trancar_escopo"),
            patch.object(persistence, "_run_publicado", return_value=anterior),
        ):
            return persistence.publicar_resultados_calculados(
                codigo_municipio_tce="010",
                exercicio_orcamento=202500,
                algoritmo_versao="despesas-v1",
                registros=registros or [_empenho(), _anulacao(), _pagamento()],
                fontes=fontes
                or {"tce_despesa_run_ids": [10, 11], "tce_pagamento_run_ids": [12]},
            )

    def test_publica_pais_antes_dos_filhos_e_grava_vinculos(self) -> None:
        resultado = self._publicar()

        self.assertFalse(resultado.reutilizado)
        self.assertEqual(resultado.quantidade_empenhos, 1)
        self.assertEqual(resultado.quantidade_anulacoes, 1)
        self.assertEqual(resultado.quantidade_pagamentos, 1)
        fatos = [x for x in self.sessao.adicionados if isinstance(x, TceDespesaCalculada)]
        empenho = next(x for x in fatos if x.natureza == "EMPENHO")
        filhos = [x for x in fatos if x.natureza != "EMPENHO"]
        self.assertTrue(all(item.registro_pai_id == empenho.id for item in filhos))

    def test_reprocessamento_identico_reutiliza_run_sem_duplicar(self) -> None:
        primeiro = self._publicar()
        run = next(
            x for x in self.sessao.adicionados if isinstance(x, TceDespesaCalculadaRun)
        )
        self.sessao = _SessaoFalsa()

        segundo = self._publicar(
            anterior=run,
            fontes={"tce_pagamento_run_ids": [12], "tce_despesa_run_ids": [11, 10]},
        )

        self.assertTrue(segundo.reutilizado)
        self.assertEqual(segundo.run_id, primeiro.run_id)
        self.assertFalse(self.sessao.adicionados)

    def test_correcao_substitui_run_sem_apagar_historico(self) -> None:
        anterior = TceDespesaCalculadaRun(
            id=50,
            escopo_chave="TCE-CE:010:202500",
            codigo_municipio_tce="010",
            exercicio_orcamento=202500,
            algoritmo_versao="despesas-v1",
            input_hash="hash-anterior",
            status="PUBLICADO",
            quantidade_empenhos=1,
            quantidade_anulacoes=0,
            quantidade_pagamentos=0,
            fontes={},
            iniciado_em=self.agora,
            publicado_em=self.agora,
        )

        resultado = self._publicar(
            anterior=anterior,
            registros=[
                _empenho(
                    valor_centavos=80_000,
                    valor_anulado_centavos=20_000,
                    valor_liquido_centavos=80_000,
                ),
                _anulacao(valor_centavos=20_000),
                _pagamento(),
            ],
        )

        self.assertEqual(anterior.status, "SUBSTITUIDO")
        self.assertEqual(anterior.substituido_em, self.agora)
        self.assertEqual(resultado.run_anterior_id, 50)
        self.assertEqual(resultado.status, "PUBLICADO")


class ModeloCalculadoTest(unittest.TestCase):
    def test_modelo_protege_escopo_vigente_e_vinculo_interno(self) -> None:
        indices = {indice.name for indice in TceDespesaCalculadaRun.__table__.indexes}
        self.assertIn("uq_tce_despesa_calculo_run_publicado_escopo", indices)
        destinos = {fk.target_fullname for fk in TceDespesaCalculada.__table__.foreign_keys}
        self.assertIn("tce_despesa_calculo_runs.id", destinos)
        self.assertIn("tce_despesas_calculadas.id", destinos)


class _ResultadoVazio:
    def all(self):
        return []


class _SessaoConsulta:
    def __init__(self) -> None:
        self.consulta = None

    def execute(self, consulta):
        self.consulta = consulta
        return _ResultadoVazio()


class ConsultaCalculadaTest(unittest.TestCase):
    def test_consulta_vigente_aplica_dimensoes_sem_escrever(self) -> None:
        sessao = _SessaoConsulta()

        @contextmanager
        def contexto():
            yield sessao

        with (
            patch.object(persistence.database, "init_db"),
            patch.object(persistence.orm, "main_session", side_effect=contexto),
        ):
            resultado = persistence.listar_resultados_calculados(
                codigo_municipio_tce="010",
                exercicio_orcamento=202500,
                competencia_inicial="2025-01",
                competencia_final="2025-12",
                naturezas=["EMPENHO"],
                portes=["ME"],
                documento_fornecedor="10496308000123",
                codigo_orgao="17",
                naturezas_despesa=["52"],
            )

        self.assertEqual(resultado, [])
        assert sessao.consulta is not None
        sql = str(
            sessao.consulta.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("tce_despesa_calculo_runs.status = 'PUBLICADO'", sql)
        self.assertIn("tce_despesas_calculadas.documento_fornecedor", sql)
        self.assertIn("tce_despesas_calculadas.codigo_orgao", sql)
        self.assertIn("tce_despesas_calculadas.natureza_despesa_codigo", sql)

    def test_historico_nao_restringe_status_do_run(self) -> None:
        sessao = _SessaoConsulta()

        @contextmanager
        def contexto():
            yield sessao

        with (
            patch.object(persistence.database, "init_db"),
            patch.object(persistence.orm, "main_session", side_effect=contexto),
        ):
            persistence.listar_historico_registro(
                natureza="EMPENHO",
                chave_registro=CHAVE_EMPENHO,
            )

        assert sessao.consulta is not None
        sql = str(sessao.consulta.compile(dialect=postgresql.dialect()))
        self.assertNotIn("tce_despesa_calculo_runs.status =", sql)
        self.assertIn("tce_despesa_calculo_runs.status AS run_status", sql)


if __name__ == "__main__":
    unittest.main()
