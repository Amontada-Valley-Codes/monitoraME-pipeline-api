from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.pncp.scheduler import ConfiguracaoAgendamentoPncp, MODALIDADES_PNCP_PADRAO


class ConfiguracaoAgendamentoPncpTest(unittest.TestCase):
    def test_padrao_restringe_ao_ceara_e_habilita_todas_modalidades(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = ConfiguracaoAgendamentoPncp.do_ambiente()

        self.assertEqual(config.ufs, ("CE",))
        self.assertEqual(config.modalidades, tuple(range(1, 15)))
        self.assertEqual(config.backfill_start_year, 2026)
        self.assertEqual(config.backfill_window_days, 7)
        self.assertEqual(MODALIDADES_PNCP_PADRAO, config.modalidades)

    def test_ambiente_pode_sobrescrever_recorte_padrao(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PNCP_SCHEDULER_UFS": "CE",
                "PNCP_SCHEDULER_MODALIDADES": "6,8",
                "PNCP_SCHEDULER_BACKFILL_START_YEAR": "2026",
            },
            clear=True,
        ):
            config = ConfiguracaoAgendamentoPncp.do_ambiente()

        self.assertEqual(config.ufs, ("CE",))
        self.assertEqual(config.modalidades, (6, 8))


if __name__ == "__main__":
    unittest.main()
