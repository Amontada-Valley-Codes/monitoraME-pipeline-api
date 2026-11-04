from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.models import RawPncpRecord
from app.pncp.normalization import ErroNormalizacaoPncp, normalizar_payload

class NormalizacaoPncpTest(unittest.TestCase):
    def test_normaliza_campos_opcionais_e_referencia_raw(self) -> None:
        raw = RawPncpRecord(id=1, source="PNCP", entity_type="CONTRATACAO", external_id="id-1", collected_at=datetime.now(timezone.utc), source_updated_at=None, payload_hash="h", payload={"numeroControlePNCP":"id-1", "objetoCompra":"objeto"}, run_id=None)
        dado = normalizar_payload(raw)
        self.assertEqual(dado["raw_record_id"], 1)
        self.assertIn("id-1", dado["url_oficial"])
        self.assertIsNone(dado["prazo_proposta_final"])
    def test_url_oficial_usa_cnpj_ano_e_sequencial_com_seis_digitos(self) -> None:
        external_id = "00394460000141-1-000105/2026"
        raw = RawPncpRecord(id=1, source="PNCP", entity_type="CONTRATACAO", external_id=external_id,
            collected_at=datetime.now(timezone.utc), source_updated_at=None, payload_hash="h",
            payload={"numeroControlePNCP": external_id, "orgaoEntidade": {"cnpj": "00394460000141"},
                "anoCompra": 2026, "sequencialCompra": 105, "objetoCompra": "objeto",
                "linkSistemaOrigem": "https://sistema-origem.exemplo/compra/105"}, run_id=None)
        dado = normalizar_payload(raw)
        self.assertEqual(dado["url_oficial"], "https://pncp.gov.br/app/editais/00394460000141/2026/000105")
    def test_quarentena_para_id_invalido(self) -> None:
        raw = RawPncpRecord(id=1, source="PNCP", entity_type="CONTRATACAO", external_id="id", collected_at=datetime.now(timezone.utc), source_updated_at=None, payload_hash="h", payload={}, run_id=None)
        with self.assertRaises(ErroNormalizacaoPncp): normalizar_payload(raw)
