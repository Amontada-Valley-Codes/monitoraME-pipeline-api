from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.models import PncpOportunidade
from app.pncp.enrichment import ClienteEnriquecimentoPncp, ErroEnriquecimentoPncp, IdentidadeCompra, identidade_do_payload, oportunidade_relevante


class EnriquecimentoPncpTest(unittest.TestCase):
    def test_identidade_exige_chaves_proprias_da_compra(self) -> None:
        identidade = identidade_do_payload({"orgaoEntidade": {"cnpj": "12.345.678/0001-90"}, "anoCompra": 2026, "sequencialCompra": 7})
        self.assertEqual(identidade, IdentidadeCompra("12345678000190", 2026, 7))

    def test_oportunidade_basica_nao_exige_enriquecimento(self) -> None:
        oportunidade = PncpOportunidade(source="PNCP", external_id="x", raw_record_id=1, payload_hash="h", source_updated_at=None, orgao=None, unidade=None, objeto="objeto", modalidade=None, situacao=None, prazo_proposta_final=None, url_oficial="https://pncp.gov.br/x")
        self.assertFalse(oportunidade_relevante(oportunidade))
        self.assertTrue(oportunidade_relevante(oportunidade, solicitada=True))

    def test_cliente_usa_rotas_oficiais_sem_pca(self) -> None:
        resposta = MagicMock(status_code=200); resposta.json.return_value = []
        cliente = ClienteEnriquecimentoPncp(base_url="https://pncp.exemplo", session=MagicMock())
        cliente.session.get.return_value = resposta
        cliente.buscar(IdentidadeCompra("12345678000190", 2026, 7), "documents")
        url = cliente.session.get.call_args.args[0]
        self.assertEqual(url, "https://pncp.exemplo/v1/orgaos/12345678000190/compras/2026/7/arquivos")
        self.assertNotIn("pca", url)

    def test_falha_de_documento_e_independente_da_contratacao(self) -> None:
        resposta = MagicMock(status_code=500)
        cliente = ClienteEnriquecimentoPncp(session=MagicMock()); cliente.session.get.return_value = resposta
        with self.assertRaises(ErroEnriquecimentoPncp) as contexto:
            cliente.buscar(IdentidadeCompra("12345678000190", 2026, 7), "documents")
        self.assertTrue(contexto.exception.retryable)
