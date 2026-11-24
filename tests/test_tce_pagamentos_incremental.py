from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key, value in {
    "DATABASE_URL": "postgresql://postgres:postgres@localhost/test",
    "LOG_DATABASE_URL": "postgresql://postgres:postgres@localhost/logs",
    "TCE_CE_BASE_URL": "https://api-dados-abertos.tce.ce.gov.br/sim",
    "OPENCNPJ_BASE_URL": "https://api.opencnpj.org",
    "IBGE_LOCALIDADES_BASE_URL": "https://servicodados.ibge.gov.br/api/v1/localidades",
    "UF_PADRAO": "CE", "CODIGO_MUNICIPIO_TCE_PADRAO": "004",
}.items():
    os.environ.setdefault(key, value)

from app.pipeline.cleaners.tce_despesas import mapear_nota_pagamento
from app.pipeline.ingestion import tce
from app.pipeline.persistence import tce_pagamentos as persistence
from app.pipeline.tce_pagamentos import executar_ingestao_competencia_pagamentos_tce
from scripts.ingestir_tce_pagamentos import _intervalo_competencias


def pagamento(**overrides):
    return {
        "codigo_municipio": "004",
        "exercicio_orcamento": 202500,
        "data_referencia_doc": 202602,
        "codigo_orgao": "01",
        "codigo_unidade_orcamentaria": "01",
        "data_emissao_empenho": "2025-05-15T00:00:00",
        "numero_empenho": "15050002",
        "numero_sub_empenho": "001",
        "numero_nota_pagamento": "0001",
        "data_nota_pagamento": "2026-02-02T00:00:00",
        "estado_de_estornado": 0,
        "valor_nota_pagamento": "10000.00",
        **overrides,
    }


class FakeSession:
    def __init__(self, publicado=None):
        self.publicado = publicado
        self.adicionados = []
        self.execucoes = []
        self._id = 1

    def execute(self, *args, **kwargs):
        self.execucoes.append((args, kwargs))

    def scalar(self, _query):
        return self.publicado

    def add(self, item):
        self.adicionados.append(item)

    def add_all(self, items):
        self.adicionados.extend(list(items))

    def flush(self):
        for item in self.adicionados:
            if getattr(item, "id", None) is None:
                item.id = self._id
                self._id += 1


class IngestaoIncrementalPagamentosTest(unittest.TestCase):
    def test_analise_deduplica_repeticao_identica_e_rejeita_conflito(self):
        publicado_em = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)
        run = SimpleNamespace(
            id=1,
            competencia="2025-01",
            publicado_em=publicado_em,
        )
        classificacoes = [
            SimpleNamespace(codigo_orgao="01", classificacao="EXECUTIVO_MUNICIPAL"),
            SimpleNamespace(codigo_orgao="02", classificacao="CAMARA"),
        ]
        base_pagamento = {
            "chave_empenho": "empenho-1", "run_id": 1,
            "chave_pagamento": "pagamento-1", "codigo_orgao": "01",
            "estado_de_estornado": "0", "data_referencia_doc": 202501,
            "data_nota_pagamento": date(2025, 1, 15),
            "valor_nota_pagamento_centavos": 1000,
        }
        empenho = SimpleNamespace(
            chave_empenho="empenho-1", codigo_elemento_despesa="33903000",
            cnpj_fornecedor="02530104000133",
            documento_fornecedor="02530104000133",
            tipo_documento_fornecedor="1", nome_fornecedor="Fornecedor",
            valor_empenhado_centavos=1000,
        )

        def executar(pagamentos):
            sessao = MagicMock()
            sessao.scalars.side_effect = [
                SimpleNamespace(all=lambda: [run]),
                SimpleNamespace(all=lambda: classificacoes),
                SimpleNamespace(all=lambda: pagamentos),
            ]
            sessao.execute.return_value.all.return_value = [(empenho, "2025-01")]
            with (
                patch.object(persistence.database, "init_db"),
                patch.object(
                    persistence.orm, "main_session", return_value=nullcontext(sessao)
                ),
            ):
                publicacoes_concluidas_em = []
                resultado = persistence.listar_pagamentos_para_analise(
                    codigo_municipio_tce="004", exercicio_orcamento=202500,
                    data_inicial=date(2025, 1, 1), data_final=date(2025, 1, 31),
                    publicacoes_concluidas_em=publicacoes_concluidas_em,
                )
                return *resultado, publicacoes_concluidas_em

        registros, ausentes, publicacoes = executar([
            SimpleNamespace(**base_pagamento), SimpleNamespace(**base_pagamento),
        ])
        self.assertEqual(ausentes, [])
        self.assertEqual(len(registros), 1)
        self.assertEqual(publicacoes, [publicado_em])

        conflitante = {**base_pagamento, "valor_nota_pagamento_centavos": 2000}
        with self.assertRaisesRegex(ValueError, "conteudo conflitante"):
            executar([
                SimpleNamespace(**base_pagamento), SimpleNamespace(**conflitante),
            ])

    def test_analise_ignora_pagamento_sem_empenho_publicado(self):
        run = SimpleNamespace(id=1, competencia="2025-01")
        classificacoes = [
            SimpleNamespace(codigo_orgao="01", classificacao="EXECUTIVO_MUNICIPAL", confianca="ALTA"),
            SimpleNamespace(codigo_orgao="02", classificacao="CAMARA", confianca="ALTA"),
        ]
        pagamentos = [
            SimpleNamespace(
                chave_empenho=chave, run_id=1, chave_pagamento=f"pagamento-{chave}",
                codigo_orgao="01", estado_de_estornado="0",
                data_referencia_doc=202501, data_nota_pagamento=date(2025, 1, 15),
                valor_nota_pagamento_centavos=valor,
            )
            for chave, valor in (("presente", 1000), ("ausente", 5000))
        ]
        empenho = SimpleNamespace(
            chave_empenho="presente", codigo_elemento_despesa="33903000",
            cnpj_fornecedor="12345678000100", documento_fornecedor="12345678000100",
            tipo_documento_fornecedor="2", nome_fornecedor="Fornecedor",
            valor_empenhado_centavos=1000,
        )
        sessao = MagicMock()
        sessao.scalars.side_effect = [
            SimpleNamespace(all=lambda: [run]),
            SimpleNamespace(all=lambda: classificacoes),
            SimpleNamespace(all=lambda: pagamentos),
        ]
        sessao.execute.return_value.all.return_value = [(empenho, "2025-01")]

        with (
            patch.object(persistence.database, "init_db"),
            patch.object(persistence.orm, "main_session", return_value=nullcontext(sessao)),
        ):
            registros, ausentes = persistence.listar_pagamentos_para_analise(
                codigo_municipio_tce="004", exercicio_orcamento=202500,
                data_inicial=date(2025, 1, 1), data_final=date(2025, 1, 31),
            )

        self.assertEqual(registros, [])
        self.assertEqual(ausentes, [])

    def test_coleta_divide_por_orgao_e_unidade_quando_teto_e_atingido(self):
        def registro(numero, unidade):
            return {
                "numero": str(numero),
                "codigo_municipio": "004",
                "exercicio_orcamento": 202500,
                "data_referencia_doc": 202602,
                "codigo_orgao": "01",
                "codigo_unidade_orcamentaria": unidade,
            }

        unidade_01 = [registro(i, "01") for i in range(600)]
        unidade_02 = [registro(i, "02") for i in range(600, 1005)]
        completos = unidade_01 + unidade_02
        pagina_limitada = completos[:1000]
        orgaos = [
            {"codigo_orgao": "01", "codigo_tipo_unidade": "01"},
            {"codigo_orgao": "77", "codigo_tipo_unidade": "02"},
        ]
        unidades = [
            {"codigo_orgao": "01", "codigo_unidade_orcamentaria": "01"},
            {"codigo_orgao": "01", "codigo_unidade_orcamentaria": "02"},
        ]

        with (
            patch(
                "app.pipeline.ingestion.tce.buscar_documentos_exercicio",
                side_effect=[orgaos, unidades],
            ),
            patch(
                "app.pipeline.ingestion.tce.buscar_dados_tce",
                side_effect=[
                    {"elements": pagina_limitada},
                    {"elements": pagina_limitada},
                    {"elements": unidade_01},
                    {"elements": unidade_02},
                ],
            ) as buscar,
            patch("app.core.logging.log_database.registrar_log_ingestao"),
        ):
            registros = tce.buscar_notas_pagamentos(
                "2026-02", exercicio_orcamento=2025, codigo_municipio="004",
            )

        self.assertEqual(len(registros), 1005)
        self.assertEqual(len({item["numero"] for item in registros}), 1005)
        self.assertEqual(
            [call.args[1]["$start_index"] for call in buscar.call_args_list],
            [0, 0, 0, 0],
        )
        for call in buscar.call_args_list:
            self.assertEqual(call.args[1]["codigo_municipio"], "004")
            self.assertEqual(call.args[1]["exercicio_orcamento"], 202500)
            self.assertEqual(call.args[1]["data_referencia_doc"], 202602)

    def test_pagamento_de_fevereiro_2026_preserva_exercicio_orcamentario_202500(self):
        normalizado = mapear_nota_pagamento(
            pagamento(), codigo_municipio_tce="004", exercicio_orcamento=2025,
            competencia="2026-02",
        )
        self.assertEqual(normalizado["exercicio_orcamento"], 202500)
        self.assertEqual(normalizado["data_referencia_doc"], 202602)
        self.assertEqual(normalizado["numero_empenho"], "15050002")
        self.assertEqual(normalizado["data_nota_pagamento"], "2026-02-02")
        self.assertEqual(normalizado["valor_nota_pagamento_centavos"], 1_000_000)

    def test_intervalo_acarau_inclui_202501_a_202609(self):
        competencias = _intervalo_competencias("2025-01", "2026-09")
        self.assertEqual(len(competencias), 21)
        self.assertEqual(competencias[0], "2025-01")
        self.assertEqual(competencias[-1], "2026-09")
        self.assertEqual(competencias[12], "2026-01")

    def _publicar(self, registros, sessao):
        with (
            patch.object(persistence.database, "init_db"),
            patch.object(persistence.orm, "main_session", return_value=nullcontext(sessao)),
        ):
            return persistence.publicar_lote(
                codigo_municipio_tce="004", exercicio_orcamento=2025,
                competencia="2026-02", pagamentos=registros,
            )

    def test_reexecucao_deduplica_e_atualizacao_substitui_snapshot_sem_regressao(self):
        primeira = mapear_nota_pagamento(
            pagamento(), codigo_municipio_tce="004", exercicio_orcamento=2025,
            competencia="2026-02",
        )
        sessao_inicial = FakeSession()
        run_inicial = self._publicar([primeira, primeira], sessao_inicial)
        self.assertEqual(run_inicial.status, persistence.STATUS_PUBLICADO)
        self.assertEqual(run_inicial.quantidade_pagamentos, 1)
        lote_anterior = next(x for x in sessao_inicial.adicionados if isinstance(x, persistence.TcePagamentoIngestionRun))
        nota_anterior = next(x for x in sessao_inicial.adicionados if isinstance(x, persistence.TcePagamento))

        sessao_reexecucao = FakeSession(publicado=lote_anterior)
        run_reexecutado = self._publicar([primeira], sessao_reexecucao)
        lote_reexecutado = next(x for x in sessao_reexecucao.adicionados if isinstance(x, persistence.TcePagamentoIngestionRun))
        nota_reexecutada = next(x for x in sessao_reexecucao.adicionados if isinstance(x, persistence.TcePagamento))
        self.assertEqual(run_reexecutado.status, persistence.STATUS_PUBLICADO)
        self.assertEqual(lote_anterior.status, persistence.STATUS_SUBSTITUIDO)
        self.assertEqual(nota_reexecutada.chave_pagamento, nota_anterior.chave_pagamento)
        self.assertEqual(nota_reexecutada.valor_nota_pagamento_centavos, nota_anterior.valor_nota_pagamento_centavos)

        atualizada = mapear_nota_pagamento(
            pagamento(valor_nota_pagamento="12000.00"), codigo_municipio_tce="004",
            exercicio_orcamento=2025, competencia="2026-02",
        )
        sessao_atualizacao = FakeSession(publicado=lote_reexecutado)
        run_atualizado = self._publicar([atualizada], sessao_atualizacao)
        lote_atual = next(x for x in sessao_atualizacao.adicionados if isinstance(x, persistence.TcePagamentoIngestionRun))
        nota_atual = next(x for x in sessao_atualizacao.adicionados if isinstance(x, persistence.TcePagamento))

        self.assertEqual(run_atualizado.status, persistence.STATUS_PUBLICADO)
        self.assertEqual(lote_reexecutado.status, persistence.STATUS_SUBSTITUIDO)
        self.assertEqual(lote_atual.status, persistence.STATUS_PUBLICADO)
        self.assertEqual(nota_anterior.valor_nota_pagamento_centavos, 1_000_000)
        self.assertEqual(nota_atual.valor_nota_pagamento_centavos, 1_200_000)
        self.assertEqual(nota_atual.chave_pagamento, nota_anterior.chave_pagamento)
        self.assertEqual(lote_atual.exercicio_orcamento, 202500)
        self.assertEqual(nota_atual.numero_empenho, "15050002")
        self.assertEqual(nota_atual.data_nota_pagamento.isoformat(), "2026-02-02")

    def test_snapshot_rejeitado_preserva_publicado(self):
        existente = persistence.TcePagamentoIngestionRun(
            codigo_municipio_tce="004", exercicio_orcamento=202500,
            competencia="2026-02", status=persistence.STATUS_PUBLICADO,
            iniciado_em=datetime.now(timezone.utc),
            quantidade_pagamentos=1,
        )
        primeiro = mapear_nota_pagamento(
            pagamento(), codigo_municipio_tce="004", exercicio_orcamento=2025,
            competencia="2026-02",
        )
        conflito = {**primeiro, "valor_nota_pagamento_centavos": 2_000_000}
        sessao = FakeSession(publicado=existente)
        resultado = self._publicar([primeiro, conflito], sessao)
        novo = next(x for x in sessao.adicionados if isinstance(x, persistence.TcePagamentoIngestionRun))
        self.assertEqual(resultado.status, persistence.STATUS_REJEITADO)
        self.assertEqual(existente.status, persistence.STATUS_PUBLICADO)
        self.assertEqual(novo.status, persistence.STATUS_REJEITADO)
        self.assertFalse(any(isinstance(x, persistence.TcePagamento) for x in sessao.adicionados))

    @patch("app.pipeline.tce_pagamentos.persistence.publicar_lote")
    @patch("app.pipeline.tce_pagamentos.cleaner.mapear_notas_pagamentos")
    @patch("app.pipeline.tce_pagamentos.tce.buscar_notas_pagamentos")
    def test_pipeline_passa_orcamento_2025_separado_da_competencia_2026_02(
        self, buscar, mapear, publicar,
    ):
        buscar.return_value = [pagamento()]
        mapear.return_value = [{"normalizado": True}]
        publicar.return_value = MagicMock(status="PUBLICADO")
        resultado = executar_ingestao_competencia_pagamentos_tce(
            "2026-02", exercicio_orcamento=2025, codigo_municipio_tce="004",
        )
        self.assertIs(resultado, publicar.return_value)
        buscar.assert_called_once_with(
            "2026-02", exercicio_orcamento=2025, codigo_municipio="004",
        )
        self.assertEqual(mapear.call_args.kwargs["exercicio_orcamento"], 2025)
        self.assertEqual(publicar.call_args.kwargs["exercicio_orcamento"], 2025)


if __name__ == "__main__":
    unittest.main()
