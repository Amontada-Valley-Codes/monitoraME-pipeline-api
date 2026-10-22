from decimal import Decimal
import unittest

from app.pipeline.cleaners.tce_pagamentos import (
    classificar_administracao,
    mapear_nota_pagamento,
    mapear_orgao,
)


class TcePagamentosCleanersTest(unittest.TestCase):
    def test_classifica_camara_pelo_nome_cadastral(self) -> None:
        self.assertEqual(classificar_administracao("Câmara Municipal de Acaraú"), "CAMARA")
        self.assertEqual(classificar_administracao("Secretaria de Saúde"), "PREFEITURA")

    def test_mapeia_orgao_preservando_codigo(self) -> None:
        orgao = mapear_orgao({
            "codigo_municipio": "004",
            "exercicio_orcamento": 202500,
            "codigo_orgao": "01",
            "nome_orgao": "Prefeitura Municipal de Acaraú",
            "codigo_tipo_unidade": "1",
            "cgc_orgao": "07547821000191",
        })
        self.assertEqual(orgao["administracao"], "PREFEITURA")
        self.assertEqual(orgao["codigo_orgao"], "01")

    def test_pagamento_de_2026_pode_pertencer_ao_orcamento_2025(self) -> None:
        pagamento = mapear_nota_pagamento({
            "codigo_municipio": "004",
            "exercicio_orcamento": 202500,
            "codigo_orgao": "01",
            "codigo_unidade_orcamentaria": "01",
            "data_emissao_empenho": "2025-12-15",
            "numero_empenho": "E1",
            "numero_sub_empenho": "0",
            "numero_nota_pagamento": "P1",
            "data_referencia_doc": 202602,
            "numero_documento_caixa": "C1",
            "data_nota_pagamento": "2026-02-10",
            "valor_nota_pagamento": Decimal("123.45"),
            "estado_de_estornado": "0",
        })
        self.assertEqual(pagamento["exercicio_orcamento"], 202500)
        self.assertEqual(pagamento["competencia_documento"], "2026-02")
        self.assertEqual(pagamento["valor_pagamento_centavos"], 12345)
        self.assertTrue(pagamento["chave_pagamento"].endswith("|0|P1"))

    def test_rejeita_exercicio_fora_do_formato_aaaa00(self) -> None:
        with self.assertRaisesRegex(ValueError, "AAAA00"):
            mapear_orgao({
                "codigo_municipio": "004",
                "exercicio_orcamento": 2025,
                "codigo_orgao": "01",
                "nome_orgao": "Prefeitura",
            })


if __name__ == "__main__":
    unittest.main()
