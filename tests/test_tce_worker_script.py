from __future__ import annotations

from pathlib import Path
import sys
import unittest
from threading import Event
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import tce_worker


class TceWorkerScriptTest(unittest.TestCase):
    def test_slot_principal_reativa_falhas_orfas_legadas_ao_iniciar(self) -> None:
        worker = MagicMock()
        worker.executar_uma_vez.return_value = False
        with (
            patch("scripts.tce_worker.WorkerTce", return_value=worker),
            patch("scripts.tce_worker.recuperar_falhas_orfas_legadas", return_value=[7, 8]) as recuperar,
            patch("scripts.tce_worker.recuperar_leases_expirados", return_value=0),
            patch("scripts.tce_worker.time.monotonic", return_value=100),
        ):
            tce_worker._executar_slot(1, Event(), once=True)

        recuperar.assert_called_once_with()
        worker.executar_uma_vez.assert_called_once_with()

    def test_outros_slots_nao_disparam_recuperacao_de_inicio(self) -> None:
        worker = MagicMock()
        worker.executar_uma_vez.return_value = False
        with (
            patch("scripts.tce_worker.WorkerTce", return_value=worker),
            patch("scripts.tce_worker.recuperar_falhas_orfas_legadas") as recuperar,
            patch("scripts.tce_worker.time.monotonic", return_value=100),
        ):
            tce_worker._executar_slot(2, Event(), once=True)

        recuperar.assert_not_called()
        worker.executar_uma_vez.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
