from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp.retry import PoliticaRetryPncp, decidir_retry, retry_after_para_timedelta


class RetryPncpTest(unittest.TestCase):
    def test_full_jitter_fica_entre_zero_e_teto_exponencial(self) -> None:
        politica = PoliticaRetryPncp(initial_delay_seconds=2, max_delay_seconds=60)

        atraso = politica.atraso_com_jitter(attempts=3, aleatorio=lambda inicio, fim: fim / 2)

        self.assertEqual(atraso, timedelta(seconds=4))

    def test_retry_after_tem_precedencia_sobre_jitter(self) -> None:
        decisao = decidir_retry(
            attempts=1, max_attempts=5, retryable=True, retry_after=timedelta(seconds=21),
        )

        self.assertTrue(decisao.repetir)
        self.assertEqual(decisao.atraso, timedelta(seconds=21))
        self.assertEqual(decisao.motivo, "Retry-After")

    def test_retry_after_http_date_e_suportado(self) -> None:
        atraso = retry_after_para_timedelta(
            "Wed, 21 Oct 2026 00:00:10 GMT",
            agora=datetime(2026, 10, 21, tzinfo=timezone.utc),
        )

        self.assertEqual(atraso, timedelta(seconds=10))
