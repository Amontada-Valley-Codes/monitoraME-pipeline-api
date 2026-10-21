from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp import jobs
from app.pncp.retry import PoliticaRetryPncp
from app.pncp.protecao import PermissaoFonte, PncpProtegidoError


class JobPncpTest(unittest.TestCase):
    def test_reserva_usa_skip_locked_e_lease_token(self) -> None:
        session = MagicMock()
        session.execute.return_value.mappings.return_value.first.return_value = {
            "id": 7, "job_type": "normalize", "entity_type": "CONTRATACAO",
            "source": "PNCP", "scope_key": "1", "dedupe_key": "normalizar:1",
            "attempts": 1, "max_attempts": 5, "lease_token": "token", "raw_record_id": 1,
            "window_start": None, "window_end": None, "page": None,
        }

        reservado = jobs.reivindicar_proximo_job_session(session, worker_id="worker-a")

        self.assertEqual(reservado.id, 7)
        statement, parametros = session.execute.call_args.args
        self.assertIn("FOR UPDATE SKIP LOCKED", statement.text)
        self.assertEqual(parametros["worker_id"], "worker-a")
        self.assertEqual(parametros["lease_seconds"], 120)
        self.assertTrue(parametros["lease_token"])

    def test_conclusao_com_token_antigo_nao_altera_job(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 0

        concluido = jobs.concluir_job_session(session, job_id=7, lease_token="antigo")

        self.assertFalse(concluido)
        statement = session.execute.call_args.args[0]
        self.assertIn("lease_token = :lease_token", statement.text)

    def test_heartbeat_tambem_confere_token_de_posse(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        renovado = jobs.renovar_lease_session(session, job_id=7, lease_token="token")

        self.assertTrue(renovado)
        statement = session.execute.call_args.args[0]
        self.assertIn("heartbeat_at = now()", statement.text)
        self.assertIn("lease_token = :lease_token", statement.text)

    def test_recuperador_devolve_lease_expirado_para_pendente(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 2

        quantidade = jobs.recuperar_leases_expirados_session(session)

        self.assertEqual(quantidade, 2)
        self.assertIn("lease_until <= now()", session.execute.call_args.args[0].text)
        self.assertIn("status = 'PENDING'", session.execute.call_args.args[0].text)

    def test_worker_executa_handler_fora_da_transacao_de_reserva(self) -> None:
        job = jobs.JobReivindicado(1, "normalize", "CONTRATACAO", "PNCP", "1", "n:1", 1, 5, "token", 1, None, None, None)
        handler = MagicMock()
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        worker = jobs.WorkerPncp("worker-a", handler)

        with (
            patch("app.pncp.jobs.reivindicar_proximo_job", return_value=job) as reivindicar,
            patch("app.pncp.jobs.orm.main_session", return_value=nullcontext(session)),
        ):
            self.assertTrue(worker.executar_uma_vez())

        handler.assert_called_once_with(job)
        reivindicar.assert_called_once()
        self.assertIn("status = 'SUCCEEDED'", session.execute.call_args.args[0].text)

    def test_agendamento_deduplica_apenas_job_ativo(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [None, 9]
        job = jobs.NovoJob(
            job_type="incremental", entity_type="CONTRATACAO", source="PNCP",
            scope_key="publicacao:2026-10-20", dedupe_key="publicacao:2026-10-20",
            priority=70, next_run_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        )

        job_id, criado = jobs.agendar_job_session(session, job)

        self.assertEqual((job_id, criado), (9, False))
        statement = session.scalar.call_args_list[0].args[0]
        self.assertIn("RETRY_WAIT", str(statement.compile()))

    def test_agendador_entrega_jobs_da_politica_para_fila(self) -> None:
        job = jobs.NovoJob(
            job_type="incremental", entity_type="CONTRATACAO", source="PNCP",
            scope_key="publicacao", dedupe_key="publicacao:1", priority=70,
            next_run_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        )
        with patch("app.pncp.jobs.agendar_job", return_value=(5, True)) as agendar:
            resultado = jobs.AgendadorPncp(lambda: [job]).executar_uma_vez()

        self.assertEqual(resultado, [(5, True)])
        agendar.assert_called_once_with(job)

    def test_falha_transitoria_reagenda_com_jitter_e_contexto(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        job = jobs.JobReivindicado(1, "incremental", "CONTRATACAO", "PNCP", "s", "d", 2, 5, "token", None, None, None, None)

        politica = PoliticaRetryPncp(initial_delay_seconds=2, max_delay_seconds=60)
        with patch.object(PoliticaRetryPncp, "atraso_com_jitter", return_value=timedelta(seconds=3.5)):
            status = jobs.falhar_job_session(
                session, job=job, erro="HTTP 503", contexto={"status_code": 503}, politica=politica,
            )

        self.assertEqual(status, jobs.RETRY_WAIT)
        _, parametros = session.execute.call_args.args
        self.assertEqual(parametros["retry_seconds"], 3.5)
        self.assertEqual(parametros["contexto"]["retry_reason"], "backoff com full jitter")

    def test_falha_permanente_nao_e_repetida(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        job = jobs.JobReivindicado(1, "incremental", "CONTRATACAO", "PNCP", "s", "d", 1, 5, "token", None, None, None, None)

        status = jobs.falhar_job_session(session, job=job, erro="HTTP 400", retryable=False)

        self.assertEqual(status, jobs.FAILED)
        _, parametros = session.execute.call_args.args
        self.assertEqual(parametros["retry_seconds"], 0)

    def test_tentativas_esgotadas_ficam_failed(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        job = jobs.JobReivindicado(1, "incremental", "CONTRATACAO", "PNCP", "s", "d", 5, 5, "token", None, None, None, None)

        status = jobs.falhar_job_session(session, job=job, erro="timeout")

        self.assertEqual(status, jobs.FAILED)

    def test_protecao_da_fonte_adia_sem_consumir_tentativa(self) -> None:
        job = jobs.JobReivindicado(1, "incremental", "CONTRATACAO", "PNCP", "s", "d", 2, 5, "token", None, None, None, None)
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        erro = PncpProtegidoError(PermissaoFonte(False, timedelta(seconds=30), "circuito aberto", "OPEN"))
        worker = jobs.WorkerPncp("worker-a", lambda _: (_ for _ in ()).throw(erro))

        with (
            patch("app.pncp.jobs.reivindicar_proximo_job", return_value=job),
            patch("app.pncp.jobs.orm.main_session", return_value=nullcontext(session)),
        ):
            worker.executar_uma_vez()

        statement, parametros = session.execute.call_args.args
        self.assertIn("attempts = GREATEST(0, attempts - 1)", statement.text)
        self.assertEqual(parametros["aguardar_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
