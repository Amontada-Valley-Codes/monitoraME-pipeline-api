from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import tce_schedule as schedule
from app.pipeline.tce_job_queue import JobTceReivindicado


def _job(tipo: str, escopo: str) -> JobTceReivindicado:
    return JobTceReivindicado(
        id=1,
        job_type=tipo,
        entity_type="TESTE",
        scope_key=escopo,
        dedupe_key="dedupe",
        attempts=1,
        max_attempts=5,
        lease_token="token",
        window_start=None,
        window_end=None,
        page=None,
    )


class TceScheduleTest(unittest.TestCase):
    def test_recuperacao_orfa_busca_empenho_e_reingere_competencia_de_origem(self) -> None:
        chave = "010|202600|04|01|2026-05-04|04050076"
        empenho_bruto = {
            "codigo_municipio": "010",
            "exercicio_orcamento": 202600,
            "codigo_orgao": "04",
            "codigo_unidade_orcamentaria": "01",
            "data_emissao_empenho": "2026-05-04T00:00:00",
            "numero_empenho": "04050076",
            "data_referencia_doc": 202605,
            "codigo_elemento_despesa": "33903000",
            "valor_empenhado": "100.00",
            "codigo_tipo_negociante": "1",
        }
        with (
            patch(
                "app.pipeline.tce_schedule.tce.buscar_documentos_exercicio",
                return_value=[empenho_bruto],
            ) as buscar,
            patch(
                "app.pipeline.tce_schedule.executar_ingestao_competencia_tce",
                return_value=SimpleNamespace(status="PUBLICADO", run_id=123),
            ) as ingerir,
            patch(
                "app.pipeline.tce_schedule.obter_empenho_publicado_por_chave",
                return_value={"chave_empenho": chave},
            ) as publicado,
        ):
            ausentes = schedule._recuperar_empenhos_referenciados(
                codigo_municipio_tce="010",
                competencia_dependente="2026-06",
                chaves_ausentes=[chave],
            )

        self.assertEqual(ausentes, [])
        buscar.assert_called_once_with(
            "notas_empenhos",
            codigo_municipio="010",
            exercicio=2026,
            codigo_orgao="04",
            codigo_unidade_orcamentaria="01",
        )
        ingerir.assert_called_once_with("2026-05", codigo_municipio_tce="010")
        publicado.assert_called_once_with(chave)

    def test_recuperacao_orfa_mantem_chave_ausente_quando_fonte_nao_a_retorna(self) -> None:
        chave = "010|202600|04|01|2026-05-04|04050076"
        with (
            patch(
                "app.pipeline.tce_schedule.tce.buscar_documentos_exercicio",
                return_value=[],
            ),
            patch("app.pipeline.tce_schedule.executar_ingestao_competencia_tce") as ingerir,
            patch(
                "app.pipeline.tce_schedule.obter_empenho_publicado_por_chave",
                return_value=None,
            ),
        ):
            ausentes = schedule._recuperar_empenhos_referenciados(
                codigo_municipio_tce="010",
                competencia_dependente="2026-06",
                chaves_ausentes=[chave],
            )

        self.assertEqual(ausentes, [chave])
        ingerir.assert_not_called()

    def test_referencia_semanal_e_domingo_as_duas_em_fortaleza(self) -> None:
        fuso = ZoneInfo("America/Fortaleza")
        horario = datetime(2026, 11, 8, 2, tzinfo=fuso)

        self.assertEqual(schedule.referencia_semanal(horario), horario)
        self.assertEqual(
            schedule.referencia_semanal(horario - timedelta(seconds=1)),
            datetime(2026, 11, 1, 2, tzinfo=fuso),
        )
        self.assertEqual(
            schedule.referencia_semanal(datetime(2026, 11, 9, 12, tzinfo=timezone.utc)),
            horario,
        )

    def test_lote_reprocessa_overlap_e_cria_backfill_ausente(self) -> None:
        publicadas = {("010", "2024-02"), ("010", "2024-03")}
        pagamentos = {
            ("010", 202400, "2024-02"),
            ("010", 202400, "2024-03"),
        }
        agora = datetime(2024, 3, 10, 5, tzinfo=timezone.utc)

        jobs = schedule.produzir_jobs_lote(
            lote="lote",
            codigos_municipios=["010"],
            anos=(2024,),
            hoje=date(2024, 3, 10),
            despesas_publicadas=publicadas,
            pagamentos_publicados=pagamentos,
            agora=agora,
            overlap_meses=2,
        )

        escopos = {job.scope_key for job in jobs}
        self.assertEqual(
            escopos,
            {
                "despesas|010|2024-01",
                "despesas|010|2024-02",
                "despesas|010|2024-03",
                "pagamentos|010|2024|2024-01",
                "pagamentos|010|2024|2024-02",
                "pagamentos|010|2024|2024-03",
                "fornecedores|2024|lote",
            },
        )
        prioridades = {job.scope_key: job.priority for job in jobs}
        self.assertEqual(prioridades["despesas|010|2024-01"], 50)
        self.assertEqual(prioridades["despesas|010|2024-03"], 90)
        self.assertEqual(prioridades["pagamentos|010|2024|2024-03"], 80)

    def test_pagamentos_preservam_exercicio_e_avancam_competencia_documental(self) -> None:
        jobs = schedule.produzir_jobs_lote(
            lote="lote",
            codigos_municipios=["010"],
            anos=(2024,),
            hoje=date(2026, 3, 1),
            despesas_publicadas=set(),
            pagamentos_publicados=set(),
            agora=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

        pagamentos = {job.scope_key: job for job in jobs if job.job_type == "tce_pagamentos"}
        self.assertIn("pagamentos|010|2024|2026-03", pagamentos)
        self.assertEqual(pagamentos["pagamentos|010|2024|2026-03"].page, 2024)

    def test_catalogo_exige_exatamente_184_municipios(self) -> None:
        ibge = [{"id": 2300000 + indice} for indice in range(184)]
        tce = [
            {
                "codigo_municipio": f"{indice:03d}",
                "codigo_municipio_ibge": 2300000 + indice,
            }
            for indice in range(184)
        ]
        with (
            patch("app.pipeline.tce_schedule.ibge.sincronizar_municipios", return_value=ibge),
            patch("app.pipeline.tce_schedule.tce.buscar_municipios", return_value=tce),
            patch("app.pipeline.tce_schedule.database.salvar_municipios_tce") as salvar,
        ):
            codigos = schedule._sincronizar_catalogo()

        self.assertEqual(len(codigos), 184)
        salvar.assert_called_once_with(tce, "CE")

        with (
            patch("app.pipeline.tce_schedule.ibge.sincronizar_municipios", return_value=ibge[:-1]),
            patch("app.pipeline.tce_schedule.tce.buscar_municipios", return_value=tce[:-1]),
        ):
            with self.assertRaisesRegex(RuntimeError, "esperado=184"):
                schedule._sincronizar_catalogo()

    def test_handler_de_despesas_exige_publicacao(self) -> None:
        session = MagicMock()
        with (
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch(
                "app.pipeline.tce_schedule.executar_ingestao_competencia_tce",
                return_value=SimpleNamespace(status="PUBLICADO"),
            ) as executar,
        ):
            schedule.executar_job(_job("tce_despesas", "despesas|010|2026-11"))
        executar.assert_called_once_with("2026-11", codigo_municipio_tce="010")

        with (
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch(
                "app.pipeline.tce_schedule.executar_ingestao_competencia_tce",
                return_value=SimpleNamespace(status="REJEITADO"),
            ),
        ):
            with self.assertRaises(schedule.FalhaPermanenteJobTce):
                schedule.executar_job(_job("tce_despesas", "despesas|010|2026-11"))

    def test_anulacao_orfa_reagenda_com_diagnostico_e_intervalo(self) -> None:
        resultado = SimpleNamespace(
            status="REJEITADO",
            run_id=91,
            problemas=[{
                "codigo": "ANULACAO_ORFA",
                "chave": "chave-empenho-1",
                "mensagem": "sem empenho publicado",
            }],
        )
        session = MagicMock()
        with (
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch("app.pipeline.tce_schedule.executar_ingestao_competencia_tce", return_value=resultado),
            patch("app.pipeline.tce_schedule.obter_empenho_publicado_por_chave", return_value=None),
            patch(
                "app.pipeline.tce_schedule._recuperar_empenhos_referenciados",
                side_effect=lambda **kwargs: kwargs["chaves_ausentes"],
            ),
        ):
            with self.assertRaises(schedule.AnulacaoOrfaAguardandoEmpenho) as erro:
                schedule.executar_job(_job("tce_despesas", "despesas|010|2026-11"))

        self.assertTrue(erro.exception.retryable)
        self.assertEqual(erro.exception.retry_after, timedelta(minutes=15))
        self.assertEqual(erro.exception.contexto["run_id_rejeitado"], 91)
        self.assertEqual(erro.exception.contexto["chaves_empenho_ausentes"], ["chave-empenho-1"])
        self.assertEqual(erro.exception.contexto["problemas_rejeicao"], resultado.problemas)

    def test_retry_orfa_verifica_dependencia_antes_de_baixar_novamente(self) -> None:
        job = _job("tce_despesas", "despesas|010|2026-11")
        job = JobTceReivindicado(**{**job.__dict__, "attempts": 1})
        rejeicao = SimpleNamespace(
            id=77,
            problemas=[{"codigo": "ANULACAO_ORFA", "chave": "empenho-a"}],
        )
        session = MagicMock()
        with (
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch("app.pipeline.tce_schedule.executar_ingestao_competencia_tce") as executar,
            patch("app.pipeline.tce_schedule.obter_empenho_publicado_por_chave", return_value=None) as buscar,
            patch(
                "app.pipeline.tce_schedule._recuperar_empenhos_referenciados",
                side_effect=lambda **kwargs: kwargs["chaves_ausentes"],
            ),
        ):
            session.scalar.return_value = rejeicao
            with self.assertRaises(schedule.AnulacaoOrfaAguardandoEmpenho) as erro:
                schedule._executar_despesas(job)

        buscar.assert_called_once_with("empenho-a")
        executar.assert_not_called()
        self.assertEqual(erro.exception.contexto["run_id_rejeitado"], 77)

    def test_retry_orfa_reprocessa_apos_publicacao_do_empenho(self) -> None:
        job = _job("tce_despesas", "despesas|010|2026-11")
        job = JobTceReivindicado(**{**job.__dict__, "attempts": 2})
        rejeicao = SimpleNamespace(
            id=77,
            problemas=[{"codigo": "ANULACAO_ORFA", "chave": "empenho-a"}],
        )
        session = MagicMock()
        with (
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch("app.pipeline.tce_schedule.obter_empenho_publicado_por_chave", return_value={"chave": "empenho-a"}) as buscar,
            patch(
                "app.pipeline.tce_schedule.executar_ingestao_competencia_tce",
                return_value=SimpleNamespace(status="PUBLICADO"),
            ) as executar,
        ):
            session.scalar.return_value = rejeicao
            schedule._executar_despesas(job)

        buscar.assert_called_once_with("empenho-a")
        executar.assert_called_once_with("2026-11", codigo_municipio_tce="010")

    def test_fornecedor_aguarda_barreira_sem_consultar_fonte(self) -> None:
        job = _job("tce_fornecedores", "fornecedores|2026|lote")
        with (
            patch("app.pipeline.tce_schedule._jobs_ingestao_ativos", return_value=3),
            patch("app.pipeline.tce_schedule.listar_cnpjs_fornecedores_publicados") as listar,
        ):
            with self.assertRaises(schedule.LoteTceAindaEmExecucao):
                schedule.executar_job(job)
        listar.assert_not_called()

    def test_job_semanal_raiz_e_idempotente_por_referencia(self) -> None:
        referencia = datetime(2026, 11, 8, 2, tzinfo=ZoneInfo("America/Fortaleza"))
        with patch(
            "app.pipeline.tce_schedule.agendar_job_unico",
            return_value=(123, True),
        ) as agendar:
            self.assertEqual(schedule.agendar_execucao_semanal(referencia), (123, True))

        job = agendar.call_args.args[0]
        self.assertEqual(job.job_type, "tce_catalogo")
        self.assertEqual(job.dedupe_key, "tce:20261108T020000-0300:catalogo")

    def test_job_catalogo_expande_filhos_em_transacao_unica(self) -> None:
        job = _job("tce_catalogo", "catalogo|lote-semanal")
        filho = MagicMock()
        session = MagicMock()
        with (
            patch(
                "app.pipeline.tce_schedule._sincronizar_catalogo",
                return_value=[f"{indice:03d}" for indice in range(184)],
            ),
            patch("app.pipeline.tce_schedule._publicacoes", return_value=(set(), set())) as publicacoes,
            patch("app.pipeline.tce_schedule.produzir_jobs_lote", return_value=[filho]) as produzir,
            patch("app.pipeline.tce_schedule.orm.main_session", return_value=nullcontext(session)),
            patch("app.pipeline.tce_schedule.agendar_lote_session", return_value=(1, 1)) as agendar,
        ):
            schedule.executar_job(job)

        publicacoes.assert_called_once()
        self.assertEqual(publicacoes.call_args.args[1], (2024, 2025, 2026))
        self.assertEqual(produzir.call_args.kwargs["lote"], "lote-semanal")
        agendar.assert_called_once_with(session, lote_id="lote-semanal", jobs=[filho])


if __name__ == "__main__":
    unittest.main()
