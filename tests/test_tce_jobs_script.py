from __future__ import annotations

from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import tce_jobs


class TceJobsScriptTest(unittest.TestCase):
    def test_replay_em_lote_por_padrao_apenas_lista_candidatos(self) -> None:
        session = MagicMock()
        candidatos = [{
            "id": 85038,
            "job_type": "tce_despesas",
            "scope_key": "despesas|026|2026-05",
            "last_error": "falha anterior",
        }]
        saida = StringIO()
        with (
            patch("scripts.tce_jobs.orm.main_session", return_value=nullcontext(session)),
            patch(
                "scripts.tce_jobs.listar_jobs_failed_tce_session",
                return_value=candidatos,
            ) as listar,
            patch("scripts.tce_jobs.replay_job_session") as replay,
            redirect_stdout(saida),
        ):
            codigo = tce_jobs.replay_failed(
                job_type="tce_despesas",
                scope_prefix="despesas|026|",
                limit=100,
                confirmar=False,
            )

        self.assertEqual(codigo, 0)
        replay.assert_not_called()
        listar.assert_called_once_with(
            session,
            job_type="tce_despesas",
            scope_prefix="despesas|026|",
            limit=100,
        )
        payload = json.loads(saida.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["replayed_count"], 0)

    def test_replay_em_lote_com_confirmacao_reativa_candidatos(self) -> None:
        session = MagicMock()
        saida = StringIO()
        with (
            patch("scripts.tce_jobs.orm.main_session", return_value=nullcontext(session)),
            patch(
                "scripts.tce_jobs.listar_jobs_failed_tce_session",
                return_value=[{"id": 85038}, {"id": 85039}],
            ),
            patch("scripts.tce_jobs.replay_job_session", side_effect=[True, False]) as replay,
            redirect_stdout(saida),
        ):
            codigo = tce_jobs.replay_failed(
                job_type="tce_despesas",
                scope_prefix="despesas|026|",
                limit=100,
                confirmar=True,
            )

        self.assertEqual(codigo, 0)
        self.assertEqual(
            [chamada.kwargs["job_id"] for chamada in replay.call_args_list],
            [85038, 85039],
        )
        payload = json.loads(saida.getvalue())
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["replayed_job_ids"], [85038])


if __name__ == "__main__":
    unittest.main()
