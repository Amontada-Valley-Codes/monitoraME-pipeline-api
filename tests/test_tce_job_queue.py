from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline import tce_job_queue as queue


def _job(*, attempts: int = 1, max_attempts: int = 5) -> queue.JobTceReivindicado:
    return queue.JobTceReivindicado(
        id=7,
        job_type="tce_despesas",
        entity_type="DESPESA",
        scope_key="despesas|010|2026-11",
        dedupe_key="tce:lote:despesas:010:2026-11",
        attempts=attempts,
        max_attempts=max_attempts,
        lease_token="token",
        window_start=None,
        window_end=None,
        page=None,
    )


class TceJobQueueTest(unittest.TestCase):
    def test_reserva_e_isolada_por_fonte_e_usa_skip_locked(self) -> None:
        session = MagicMock()
        session.execute.return_value.mappings.return_value.first.return_value = {
            "id": 7,
            "job_type": "tce_despesas",
            "entity_type": "DESPESA",
            "scope_key": "despesas|010|2026-11",
            "dedupe_key": "tce:lote:despesas:010:2026-11",
            "attempts": 1,
            "max_attempts": 5,
            "lease_token": "token",
            "window_start": None,
            "window_end": None,
            "page": None,
        }

        reservado = queue.reivindicar_proximo_job_session(
            session,
            worker_id="worker-tce",
            lease_seconds=1800,
        )

        self.assertEqual(reservado.id, 7)
        statement, parametros = session.execute.call_args.args
        self.assertIn("FOR UPDATE SKIP LOCKED", statement.text)
        self.assertIn("attempts < max_attempts", statement.text)
        self.assertIn("source = 'TCE-CE'", statement.text)
        self.assertIn("left(job_type, 4) = 'tce_'", statement.text)
        self.assertEqual(parametros["lease_seconds"], 1800)

    def test_conclusao_e_heartbeat_conferem_token(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        self.assertTrue(
            queue.renovar_lease_session(
                session,
                job_id=7,
                lease_token="token",
                lease_seconds=1800,
            )
        )
        self.assertIn("lease_token = :lease_token", session.execute.call_args.args[0].text)

        self.assertTrue(
            queue.concluir_job_session(session, job_id=7, lease_token="token")
        )
        self.assertIn("status = 'SUCCEEDED'", session.execute.call_args.args[0].text)

    def test_recuperador_nao_altera_jobs_de_outra_fonte(self) -> None:
        session = MagicMock()
        session.execute.return_value.mappings.return_value.all.return_value = [
            {"id": 7, "attempts": 2, "max_attempts": 5, "status": "PENDING"},
            {"id": 8, "attempts": 5, "max_attempts": 5, "status": "FAILED"},
        ]

        self.assertEqual(queue.recuperar_leases_expirados_session(session), 2)

        statement = session.execute.call_args.args[0]
        self.assertIn("source = 'TCE-CE'", statement.text)
        self.assertIn("lease_until <= now()", statement.text)
        self.assertIn("attempts >= max_attempts", statement.text)
        self.assertIn("THEN 'FAILED' ELSE 'PENDING'", statement.text)
        eventos = [chamada.args[0].event_type for chamada in session.add.call_args_list]
        self.assertEqual(eventos, ["LEASE_EXPIRED", "LEASE_EXPIRED_FAILED"])

    def test_falha_transitoria_reagenda_e_falha_permanente_encerra(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        status = queue.falhar_job_session(
            session,
            job=_job(attempts=2),
            erro="timeout",
            retryable=True,
            retry_after=timedelta(seconds=30),
            contexto={"categoria": "TimeoutError"},
        )

        self.assertEqual(status, queue.RETRY_WAIT)
        _, parametros = session.execute.call_args.args
        self.assertEqual(parametros["retry_seconds"], 30)
        self.assertEqual(parametros["contexto"], {"categoria": "TimeoutError"})

        status = queue.falhar_job_session(
            session,
            job=_job(),
            erro="payload rejeitado",
            retryable=False,
        )
        self.assertEqual(status, queue.FAILED)

    def test_adiamento_de_barreira_nao_consumira_tentativa(self) -> None:
        session = MagicMock()
        session.execute.return_value.rowcount = 1

        adiado = queue.adiar_job_session(
            session,
            job=_job(attempts=3),
            motivo="lote ainda ativo",
            aguardar=timedelta(minutes=15),
        )

        self.assertTrue(adiado)
        statement, parametros = session.execute.call_args.args
        self.assertIn("attempts = GREATEST(0, attempts - 1)", statement.text)
        self.assertEqual(parametros["retry_seconds"], 900)

    def test_worker_executa_handler_fora_da_transacao_de_reserva(self) -> None:
        job = _job()
        handler = MagicMock()
        session = MagicMock()
        session.execute.return_value.rowcount = 1
        worker = queue.WorkerTce(
            "worker-tce",
            handler,
            lease_seconds=1800,
        )

        with (
            patch("app.pipeline.tce_job_queue.reivindicar_proximo_job", return_value=job),
            patch("app.pipeline.tce_job_queue.orm.main_session", return_value=nullcontext(session)),
        ):
            self.assertTrue(worker.executar_uma_vez())

        handler.assert_called_once_with(job)
        self.assertIn("status = 'SUCCEEDED'", session.execute.call_args.args[0].text)

    def test_agendamento_unico_considera_todo_o_historico(self) -> None:
        session = MagicMock()
        session.scalar.return_value = 99
        job = queue.NovoJobTce(
            job_type="tce_catalogo",
            entity_type="MUNICIPIO",
            scope_key="catalogo|20261108T020000-0300",
            dedupe_key="tce:20261108T020000-0300:catalogo",
            priority=100,
            next_run_at=datetime(2026, 11, 8, 5, tzinfo=timezone.utc),
        )

        self.assertEqual(queue.agendar_job_unico_session(session, job), (99, False))

        statement = session.scalar.call_args.args[0]
        compilado = str(statement.compile())
        self.assertIn("ingestion_jobs.source", compilado)
        self.assertNotIn("ingestion_jobs.status", compilado)

    def test_replay_e_explicito_e_restrito_ao_tce(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = 7

        self.assertTrue(queue.replay_job_session(session, job_id=7))

        statement, parametros = session.execute.call_args.args
        self.assertIn("source = 'TCE-CE'", statement.text)
        self.assertIn("status = 'FAILED'", statement.text)
        self.assertIn("FOR UPDATE SKIP LOCKED", statement.text)
        self.assertEqual(parametros, {"job_id": 7})

    def test_lista_falhas_permanentes_permite_filtros_e_limite(self) -> None:
        session = MagicMock()
        session.execute.return_value.mappings.return_value.all.return_value = [
            {"id": 85038, "job_type": "tce_despesas", "scope_key": "despesas|026|2026-05"},
        ]

        jobs = queue.listar_jobs_failed_tce_session(
            session,
            job_type="tce_despesas",
            scope_prefix="despesas|026|",
            limit=25,
        )

        self.assertEqual(jobs[0]["id"], 85038)
        statement, parametros = session.execute.call_args.args
        self.assertIn("source = 'TCE-CE'", statement.text)
        self.assertIn("status = 'FAILED'", statement.text)
        self.assertIn("left(job.scope_key, length(:scope_prefix))", statement.text)
        self.assertIn("posterior.id > job.id", statement.text)
        self.assertIn("ativo.status IN ('PENDING', 'RUNNING', 'RETRY_WAIT')", statement.text)
        self.assertEqual(
            parametros,
            {
                "job_type": "tce_despesas",
                "scope_prefix": "despesas|026|",
                "limit": 25,
            },
        )

    def test_lista_falhas_permanentes_rejeita_tipo_de_outra_fonte(self) -> None:
        with self.assertRaisesRegex(ValueError, "tce_"):
            queue.listar_jobs_failed_tce_session(MagicMock(), job_type="pncp_collect")

    def test_recuperador_reabre_uma_vez_apenas_orfas_legadas_e_registra_origem(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [7, 8]
        with patch(
            "app.pipeline.tce_job_queue.replay_job_session",
            side_effect=[True, False],
        ) as replay:
            reabertos = queue.recuperar_falhas_orfas_legadas_session(session)

        self.assertEqual(reabertos, [7])
        statement, parametros = session.execute.call_args.args
        self.assertIn("last_error_context->>'categoria' = 'FalhaPermanenteJobTce'", statement.text)
        self.assertIn("problema->>'codigo' = 'ANULACAO_ORFA'", statement.text)
        self.assertIn("evento.details->>'trigger' = 'startup_orphan_failure_recovery'", statement.text)
        self.assertIn("FOR UPDATE OF job SKIP LOCKED", statement.text)
        self.assertEqual(parametros, {"limit": 1000})
        self.assertEqual(
            [
                (chamada.args[0], chamada.kwargs)
                for chamada in replay.call_args_list
            ],
            [
                (
                    session,
                    {
                        "job_id": 7,
                        "event_details": {"trigger": "startup_orphan_failure_recovery"},
                    },
                ),
                (
                    session,
                    {
                        "job_id": 8,
                        "event_details": {"trigger": "startup_orphan_failure_recovery"},
                    },
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
