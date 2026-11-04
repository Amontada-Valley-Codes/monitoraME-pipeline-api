from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp.cnae_ibge import ErroCatalogoCnaeIbge, normalizar_subclasses
from app.pncp.taxonomy import (
    adicionar_relacao,
    mapa_ativo_detalhado_para_matching,
    mapa_ativo_para_matching,
)


class PncpTaxonomyTest(unittest.TestCase):
    def test_normaliza_catalogo_ibge_com_codigo_e_descricao(self) -> None:
        resultado = normalizar_subclasses([
            {"id": "6201501", "descricao": "Desenvolvimento de programas"},
            {"id": "6201501", "descricao": "Descrição atualizada"},
            {"id": "6201", "descricao": "Classe CNAE, não subclasse"},
            {"id": "6203100", "descricao": "Licenciamento de software"},
        ])
        self.assertEqual([r["codigo"] for r in resultado], ["6201501", "6203100"])
        self.assertEqual(resultado[0]["descricao"], "Descrição atualizada")

    def test_rejeita_payload_ibge_vazio_ou_sem_subclasses(self) -> None:
        for payload in ([], {"erro": "indisponível"}, [{"id": "6201", "descricao": "classe"}]):
            with self.subTest(payload=payload), self.assertRaises(ErroCatalogoCnaeIbge):
                normalizar_subclasses(payload)

    def test_matching_recebe_somente_relacoes_ativas_da_versao_ativa(self) -> None:
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = SimpleNamespace(versao="v2")
        session.query.return_value.filter_by.return_value.all.return_value = [
            SimpleNamespace(cnae_codigo="6201501", categoria_slug="software"),
        ]
        versao, mapa = mapa_ativo_para_matching(session)
        self.assertEqual(versao, "v2")
        self.assertEqual(mapa, {"6201501": {"software"}})
        session.query.return_value.filter_by.assert_called_with(versao="v2", ativo=True)

    def test_matching_detalhado_preserva_tipo_confianca_e_condicao(self) -> None:
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = SimpleNamespace(versao="v2")
        session.query.return_value.filter_by.return_value.all.return_value = [
            SimpleNamespace(
                cnae_codigo="6201501", categoria_slug="software", tipo="CONDICIONAL",
                confianca=0.63, condicao="porte mínimo",
            ),
        ]

        version, associations = mapa_ativo_detalhado_para_matching(session)

        self.assertEqual(version, "v2")
        self.assertEqual(associations["6201501"][0].category, "software")
        self.assertEqual(associations["6201501"][0].relation_type, "CONDICIONAL")
        self.assertEqual(associations["6201501"][0].confidence, 0.63)
        self.assertEqual(associations["6201501"][0].condition, "porte mínimo")

    def test_rejeita_cnae_ausente_na_taxonomia(self) -> None:
        session = MagicMock()
        session.get.side_effect = [MagicMock(estado="RASCUNHO"), None]
        with self.assertRaisesRegex(ValueError, "CNAE inexistente"):
            adicionar_relacao(
                session, versao="v2", cnae="1234567", categoria="software",
                tipo="DIRETA", confianca=0.8, responsavel="revisor",
                evidencias=["fonte documentada"],
            )

    def test_rejeita_categoria_ausente_na_taxonomia(self) -> None:
        session = MagicMock()
        session.get.side_effect = [MagicMock(estado="RASCUNHO"), MagicMock(), None]
        with self.assertRaisesRegex(ValueError, "Categoria inexistente"):
            adicionar_relacao(
                session, versao="v2", cnae="1234567", categoria="ausente",
                tipo="DIRETA", confianca=0.8, responsavel="revisor",
                evidencias=["fonte documentada"],
            )

    def test_condicional_exige_condicao_explicita(self) -> None:
        session = MagicMock()
        session.get.side_effect = [MagicMock(estado="RASCUNHO"), MagicMock(), MagicMock()]
        with self.assertRaisesRegex(ValueError, "condição explícita"):
            adicionar_relacao(
                session, versao="v2", cnae="1234567", categoria="software",
                tipo="CONDICIONAL", confianca=0.6, responsavel="revisor",
                evidencias=["fonte documentada"],
            )


if __name__ == "__main__":
    unittest.main()
