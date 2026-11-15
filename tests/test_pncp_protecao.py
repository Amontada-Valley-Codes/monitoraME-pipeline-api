from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from app.pncp.protecao import (
    CLOSED,
    PoliticaProtecaoPncp,
    adquirir_permissao_session,
)


class ProtecaoPncpReservaTest(unittest.TestCase):
    def _controle(self, *, agora: datetime, atualizado_ha: timedelta) -> SimpleNamespace:
        return SimpleNamespace(
            source="PNCP",
            circuit_state=CLOSED,
            open_until=None,
            half_open_in_flight=0,
            concurrency_limit=1,
            active_requests=1,
            next_request_at=agora - timedelta(seconds=5),
            interval_milliseconds=500,
            successes_since_reduction=0,
            last_reason="falha transitoria HTTP 429",
            updated_at=agora - atualizado_ha,
        )

    def _session(self, controle: SimpleNamespace) -> MagicMock:
        session = MagicMock()
        session.execute.return_value.scalar_one.return_value = controle
        return session

    def test_reserva_obsoleta_e_recuperada_antes_de_reivindicar_slot(self) -> None:
        agora = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
        controle = self._controle(agora=agora, atualizado_ha=timedelta(days=5))

        permissao = adquirir_permissao_session(
            self._session(controle),
            source="PNCP",
            politica=PoliticaProtecaoPncp(concurrency_initial=1, concurrency_max=1),
            agora=agora,
        )

        self.assertTrue(permissao.permitida)
        self.assertEqual(controle.active_requests, 1)
        self.assertEqual(controle.last_reason, "reservas de requisicao expiradas recuperadas: 1")
        self.assertEqual(controle.updated_at, agora)

    def test_reserva_recente_nao_e_liberada_prematuramente(self) -> None:
        agora = datetime(2026, 11, 15, 20, tzinfo=timezone.utc)
        controle = self._controle(agora=agora, atualizado_ha=timedelta(seconds=30))

        permissao = adquirir_permissao_session(
            self._session(controle),
            source="PNCP",
            politica=PoliticaProtecaoPncp(concurrency_initial=1, concurrency_max=1),
            agora=agora,
        )

        self.assertFalse(permissao.permitida)
        self.assertEqual(permissao.motivo, "limite de concorrencia")
        self.assertEqual(controle.active_requests, 1)
        self.assertEqual(controle.updated_at, agora - timedelta(seconds=30))


if __name__ == "__main__":
    unittest.main()
