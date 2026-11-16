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
        self.assertEqual(config.flows, ("propostas_abertas",))
        self.assertEqual(config.modalidades, (*range(1, 15), 16, 17, 18))
        self.assertEqual(MODALIDADES_PNCP_PADRAO, config.modalidades)
        self.assertEqual(config.open_proposals_horizon_days, 410)

    def test_ambiente_pode_sobrescrever_recorte_padrao(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PNCP_SCHEDULER_UFS": "CE",
                "PNCP_SCHEDULER_MODALIDADES": "6,8",
                "PNCP_SCHEDULER_FLOWS": "propostas_abertas",
                "PNCP_SCHEDULER_OPEN_PROPOSALS_HORIZON_DAYS": "500",
            },
            clear=True,
        ):
            config = ConfiguracaoAgendamentoPncp.do_ambiente()

        self.assertEqual(config.ufs, ("CE",))
        self.assertEqual(config.modalidades, (6, 8))
        self.assertEqual(config.open_proposals_horizon_days, 500)

    def test_recusa_fluxos_que_incluem_editais_encerrados(self) -> None:
        with patch.dict(
            os.environ,
            {"PNCP_SCHEDULER_FLOWS": "publicacao,atualizacao"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "fluxos PNCP nao suportados"):
                ConfiguracaoAgendamentoPncp.do_ambiente()


if __name__ == "__main__":
    unittest.main()
