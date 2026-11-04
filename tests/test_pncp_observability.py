from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pncp.observability import metricas_session

class ObservabilityPncpTest(unittest.TestCase):
    def test_metricas_expoem_fila_fluxo_e_backlog(self) -> None:
        session = MagicMock()
        session.execute.side_effect = [
            type("R", (), {"mappings": lambda _: type("M", (), {"all": lambda _: [{"status":"FAILED", "total":2, "tentativas":5}]})()})(),
            type("R", (), {"mappings": lambda _: type("M", (), {"all": lambda _: [{"fluxo":"publicacao", "last_success_at":None, "lag_seconds":None, "pendentes":1}]})()})(),
            type("R", (), {"mappings": lambda _: type("M", (), {"one": lambda _: {"raw_total":3,"raw_backlog":1,"enrichment_backlog":0,"leases_expirados":2,"eventos_gerados":1}})()})(),
            type("R", (), {"mappings": lambda _: type("M", (), {"one": lambda _: {"oportunidades":3,"oportunidades_com_itens":1,"itens":2,"itens_com_lote":2,"lotes_identificados":1,"itens_resultado_ausente":1,"itens_resultado_parcial":1,"itens_resultado_final":0,"resultados":1,"documentos":2,"documentos_temporarios":1,"documentos_removidos":0,"versoes":4}})()})(),
        ]
        dados = metricas_session(session)
        self.assertEqual(dados["jobs"]["FAILED"]["tentativas"], 5)
        self.assertEqual(dados["totais"]["raw_backlog"], 1)
        self.assertEqual(dados["fluxos"][0]["fluxo"], "publicacao")
        self.assertEqual(dados["cobertura_participacao"]["itens_resultado_parcial"], 1)
        self.assertEqual(dados["cobertura_participacao"]["lotes_identificados"], 1)
