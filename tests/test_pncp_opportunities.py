from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.models import PncpEnriquecimento, PncpOportunidade
from app.pncp.opportunities import contrato_consumo, avaliar_matching_session

class OpportunitiesPncpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.o = PncpOportunidade(source="PNCP", external_id="x", raw_record_id=1, payload_hash="h", source_updated_at=None, orgao={"municipio": "Fortaleza"}, unidade=None, objeto="fornecimento de equipamentos de informatica", modalidade=None, situacao=None, prazo_proposta_final=None, url_oficial="https://pncp.gov.br/x")
    def test_contrato_e_local_e_exibivel(self) -> None:
        dto = contrato_consumo(self.o)
        self.assertEqual(dto["url_oficial"], "https://pncp.gov.br/x")
        self.assertFalse(dto["itens_disponiveis"])
    def test_matching_exige_depara_explicito(self) -> None:
        s = MagicMock(); s.query.return_value.filter_by.return_value.one_or_none.return_value = None
        match = avaliar_matching_session(s, oportunidade=self.o, profile_key="empresa:1", cnaes=["6201"], mapa_cnae_categoria={"6201": {"informatica"}}, rule_version="v1")
        self.assertTrue(match.compatible)
        self.assertIn("categorias_compativeis:informatica", match.reasons)
    def test_matching_guarda_campos_ausentes(self) -> None:
        s = MagicMock(); s.query.return_value.filter_by.return_value.one_or_none.return_value = None
        self.o.objeto = None
        match = avaliar_matching_session(s, oportunidade=self.o, profile_key="empresa:1", cnaes=[], mapa_cnae_categoria={}, rule_version="v1")
        self.assertFalse(match.compatible)
        self.assertEqual(set(match.missing_fields), {"objeto", "cnaes"})
