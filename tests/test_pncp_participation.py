from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.models import PncpOportunidade
from app.pncp.enrichment import ClienteEnriquecimentoPncp, IdentidadeCompra
from app.pncp.participation import salvar_documentos_session, salvar_itens_session, salvar_resultados_session


class SessaoFalsa:
    def __init__(self) -> None:
        self.registros: dict[tuple, object] = {}

    def get(self, modelo, chave):
        return self.registros.get((modelo.__name__, tuple(chave.values())))

    def add(self, registro):
        campos = ("source", "external_id", "numero_item", "sequencial_resultado", "sequencial_documento")
        chave = tuple(getattr(registro, campo) for campo in campos if hasattr(registro, campo))
        self.registros[(type(registro).__name__, chave)] = registro

    def scalars(self, consulta):
        retorno = MagicMock()
        tipo_consulta = consulta.column_descriptions[0]["entity"].__name__
        parametros = set(consulta.compile().params.values())
        retorno.all.return_value = [r for (tipo, _), r in self.registros.items()
            if tipo == tipo_consulta and r.external_id in parametros
            and (tipo != "PncpResultadoItem" or r.numero_item in parametros)]
        return retorno


def oportunidade(external_id: str) -> PncpOportunidade:
    return PncpOportunidade(source="PNCP", external_id=external_id, raw_record_id=1, payload_hash="hash",
        source_updated_at=datetime.now(timezone.utc), url_oficial="https://pncp.gov.br")


class ParticipacaoTest(unittest.TestCase):
    @patch("app.pncp.participation._evento")
    @patch("app.pncp.participation._versao")
    def test_itens_e_resultados_nao_se_misturam_entre_contratacoes(self, versao, evento):
        sessao = SessaoFalsa()
        a, b = oportunidade("compra-a"), oportunidade("compra-b")
        item = {"numeroItem": 1, "numeroLote": 9, "descricao": "Computador", "temResultado": True}
        salvar_itens_session(sessao, a, [item])
        salvar_itens_session(sessao, b, [item])
        self.assertEqual(len([chave for chave in sessao.registros if chave[0] == "PncpItem"]), 2)
        self.assertEqual(sessao.get(type(next(r for (tipo, _), r in sessao.registros.items() if tipo == "PncpItem")),
            {"source": "PNCP", "external_id": "compra-a", "numero_item": 1}).numero_lote, "9")
        resultado = {"numeroItem": 1, "sequencialResultado": 1, "tipoPessoa": "PJ", "niFornecedor": "12345678000190"}
        self.assertEqual(salvar_resultados_session(sessao, a, 1, [resultado])["status"], "PARCIAL")
        with self.assertRaises(ValueError):
            salvar_resultados_session(sessao, b, 1, [{**resultado, "numeroItem": 2}])
        self.assertEqual(salvar_resultados_session(sessao, b, 1, [])["status"], "AUSENTE")
        evento.reset_mock()
        salvar_itens_session(sessao, a, [item])
        salvar_resultados_session(sessao, a, 1, [resultado])
        evento.assert_not_called()
        self.assertEqual(salvar_resultados_session(sessao, a, 1, [])["status"], "AUSENTE")
        registro = next(r for (tipo, _), r in sessao.registros.items() if tipo == "PncpResultadoItem")
        self.assertEqual(registro.disponibilidade, "REMOVIDO")
        self.assertEqual(salvar_resultados_session(sessao, a, 1, [resultado])["status"], "PARCIAL")
        self.assertEqual(registro.disponibilidade, "ATUAL")

    @patch("app.pncp.participation._evento")
    @patch("app.pncp.participation._versao")
    def test_documento_passa_por_indisponivel_e_removido_sem_perder_url(self, versao, evento):
        sessao = SessaoFalsa()
        compra = oportunidade("compra-a")
        documento = {"sequencialDocumento": 3, "url": "https://pncp.gov.br/arquivo/3", "titulo": "Edital"}
        self.assertEqual(salvar_documentos_session(sessao, compra, [documento])["novos"], 1)
        self.assertEqual(salvar_documentos_session(sessao, compra, [documento])["novos"], 0)
        self.assertEqual(salvar_documentos_session(sessao, compra, [])["temporarios"], 1)
        self.assertEqual(salvar_documentos_session(sessao, compra, [])["removidos"], 1)
        registro = next(r for (tipo, _), r in sessao.registros.items() if tipo == "PncpDocumento")
        self.assertEqual(registro.url_oficial, documento["url"])
        self.assertEqual(registro.disponibilidade, "REMOVIDO")
        self.assertEqual(salvar_documentos_session(sessao, compra, [documento])["alterados"], 1)
        self.assertEqual(registro.disponibilidade, "DISPONIVEL")

    def test_cliente_percorre_paginas_antes_de_devolver_itens(self):
        sessao = MagicMock()
        primeira, segunda = MagicMock(status_code=200), MagicMock(status_code=200)
        primeira.json.return_value = [{"numeroItem": numero} for numero in range(1, 51)]
        segunda.json.return_value = [{"numeroItem": 51}]
        sessao.get.side_effect = [primeira, segunda]
        protecao = MagicMock(); protecao.adquirir.return_value.permitida = True
        cliente = ClienteEnriquecimentoPncp(base_url="https://pncp.gov.br/api/pncp", session=sessao, protecao=protecao)
        itens = cliente.buscar(IdentidadeCompra("12345678000190", 2026, 1), "items")
        self.assertEqual(len(itens), 51)
        self.assertIn("pagina=2", sessao.get.call_args.args[0])
