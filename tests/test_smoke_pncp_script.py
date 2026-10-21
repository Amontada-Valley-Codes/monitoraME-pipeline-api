from __future__ import annotations

from pathlib import Path
import unittest


class SmokePncpScriptTest(unittest.TestCase):
    def test_script_de_smoke_existe(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        self.assertTrue((raiz / "scripts" / "smoke_pncp_publicacao.py").is_file())


if __name__ == "__main__":
    unittest.main()
