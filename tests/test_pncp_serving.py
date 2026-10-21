from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.models import PncpEnriquecimento, PncpOportunidade
from app.pncp import serving


class PncpServingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.oportunidade = PncpOportunidade(
            source="PNCP", external_id="2026-1", raw_record_id=1,
            payload_hash="hash", source_updated_at=None,
            orgao={"nome": "Orgao teste"}, unidade=None,
            objeto="Aquisicao de computadores", modalidade=None, situacao=None,
            prazo_proposta_final=None, url_oficial="https://pncp.gov.br/editais/2026-1",
        )

    @patch("app.pncp.serving.orm.main_session")
    def test_lista_dados_normalizados_sem_consultar_pncp(self, main_session) -> None:
        session = main_session.return_value.__enter__.return_value
        session.scalar.return_value = 1
        session.execute.return_value.all.return_value = [(self.oportunidade, None)]

        resultado = serving.listar_editais(busca="computadores", pagina=1, tamanho_pagina=10)

        self.assertEqual(resultado["paginacao"], {"pagina": 1, "tamanho_pagina": 10, "total": 1})
        self.assertEqual(resultado["dados"][0]["id"], "2026-1")
        self.assertFalse(resultado["dados"][0]["itens_disponiveis"])

    @patch("app.pncp.serving.orm.main_session")
    def test_detalhe_expoe_enriquecimento_ja_persistido(self, main_session) -> None:
        enriquecimento = PncpEnriquecimento(
            source="PNCP", external_id="2026-1", cnpj_orgao="123",
            ano_compra=2026, sequencial_compra=1, source_version_hash="hash",
            detail_payload={"numero": "2026-1"}, detail_hash="d",
            items_payload=[{"descricao": "Computador"}], items_hash="i",
            documents_payload=None, documents_hash=None, expires_at=None,
            last_error=None,
        )
        session = main_session.return_value.__enter__.return_value
        session.get.side_effect = [self.oportunidade, enriquecimento]

        resultado = serving.obter_edital("2026-1")

        self.assertEqual(resultado["detalhe"], {"numero": "2026-1"})
        self.assertEqual(resultado["itens"], [{"descricao": "Computador"}])
        self.assertTrue(resultado["itens_disponiveis"])

