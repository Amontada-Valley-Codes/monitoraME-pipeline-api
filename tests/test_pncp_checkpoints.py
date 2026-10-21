from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pncp.checkpoints import ChaveCursor, proxima_janela

class CheckpointPncpTest(unittest.TestCase):
    def test_overlap_reconsulta_parte_da_janela_confirmada(self) -> None:
        agora = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        inicio, fim = proxima_janela(ChaveCursor("PNCP", "CONTRATACAO", "publicacao", "modalidade:6"), agora=agora, tamanho=timedelta(minutes=10), last_confirmed_end=agora - timedelta(minutes=2))
        self.assertEqual(inicio, agora - timedelta(minutes=7)); self.assertEqual(fim, agora)
    def test_cursor_inicial_usa_tamanho_planejado(self) -> None:
        agora = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        inicio, _ = proxima_janela(ChaveCursor("PNCP", "CONTRATACAO", "atualizacao", "modalidade:6"), agora=agora, tamanho=timedelta(minutes=10), last_confirmed_end=None)
        self.assertEqual(inicio, agora - timedelta(minutes=10))
