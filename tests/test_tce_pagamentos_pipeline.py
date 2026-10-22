from datetime import datetime, timezone
import unittest

from app.pipeline.pagamentos import processar_pagamentos


def _orgao(codigo: str, administracao: str = "PREFEITURA") -> dict:
    return {"codigo_orgao": codigo, "administracao": administracao}


def _empenho(chave: str, elemento: str, *, cnpj: str | None = "11444777000161", cpf: str | None = None) -> dict:
    return {
        "chave_empenho": chave,
        "codigo_elemento_despesa": elemento,
        "documento_fornecedor": cnpj or cpf,
        "cnpj_fornecedor": cnpj,
        "cpf_fornecedor": cpf,
    }


def _pagamento(chave: str, valor: int, *, orgao: str = "01", nota: str = "P1", competencia: str = "2025-01", estornado: int = 0) -> dict:
    return {
        "chave_pagamento": f"{chave}|0|{nota}",
        "chave_empenho": chave,
        "codigo_orgao": orgao,
        "competencia_documento": competencia,
        "competencia_pagamento": competencia,
        "valor_pagamento_centavos": valor,
        "estado_de_estornado": estornado,
    }


def _processar(orgaos: list[dict], pagamentos: list[dict], empenhos: list[dict], classificador=None):
    return processar_pagamentos(
        codigo_municipio_tce="004",
        codigo_municipio_ibge="2300200",
        municipio="Acaraú",
        exercicio_orcamento=202500,
        competencia_corte="2026-09",
        orgaos=orgaos,
        pagamentos=pagamentos,
        empenhos=empenhos,
        classificar_cnpj=classificador,
        referencia_cadastral={
            "fonte": "RECEITA_FEDERAL",
            "referencia": "2026-09",
            "consultado_em": datetime(2026, 10, 20, tzinfo=timezone.utc),
            "procedencia_temporal": "RETRATO_MENSAL",
        },
        atualizado_em=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )


class TcePagamentosPipelineTest(unittest.TestCase):
    def test_fluxo_filtra_camara_estorno_e_classifica_seis_categorias(self) -> None:
        empenhos = [
            _empenho("E-ME", "39"),
            _empenho("E-CPF", "30", cnpj=None, cpf="52998224725"),
            _empenho("E-INV", "35", cnpj=None),
            _empenho("E-CAM", "39"),
            _empenho("E-EST", "39"),
        ]
        pagamentos = [
            _pagamento("E-ME", 10_000, competencia="2026-02"),
            _pagamento("E-CPF", 2_000, nota="P2"),
            _pagamento("E-INV", 1_000, nota="P3"),
            _pagamento("E-CAM", 2_400_000, orgao="02", nota="P4"),
            _pagamento("E-EST", 9_000, nota="P5", estornado=1),
        ]
        resultado = _processar(
            [_orgao("01"), _orgao("02", "CAMARA")],
            pagamentos,
            empenhos,
            lambda cnpj, competencia: {
                "porte_codigo": "01", "temporal_comprovada": True
            },
        )
        self.assertTrue(resultado.publicavel)
        self.assertEqual(resultado.resposta.totais.total_financeiro_centavos, 13_000)
        self.assertEqual(resultado.resposta.totais.total_me_centavos, 10_000)
        categorias = {item.categoria.value: item.valor_centavos for item in resultado.resposta.reconciliacao}
        self.assertEqual(categorias["CPF"], 2_000)
        self.assertEqual(categorias["DOCUMENTO_INVALIDO"], 1_000)
        self.assertEqual(resultado.resposta.qualidade.status.value, "PARCIAL")

    def test_bloqueia_orfao_ambiguidade_e_duplicidade_conflitante(self) -> None:
        primeiro = _pagamento("SEM", 100)
        segundo = {**primeiro, "valor_pagamento_centavos": 200}
        resultado = _processar([_orgao("01")], [primeiro, segundo], [])
        self.assertFalse(resultado.publicavel)
        self.assertEqual(resultado.resposta.qualidade.status.value, "BLOQUEADA")
        self.assertEqual(resultado.resposta.qualidade.pagamentos_orfaos, 1)
        self.assertEqual(resultado.resposta.qualidade.duplicidades_conflitantes, 1)

    def test_regressao_acarau_total_exato_e_camara_excluida(self) -> None:
        valores = {
            "39": 5_000_000_000,
            "51": 4_000_000_000,
            "30": 2_000_000_000,
            "52": 1_000_000_000,
            "32": 500_000_000,
            "35": 1_000_000_000,
            "40": 31_152_944,
        }
        empenhos = [_empenho(f"E-{codigo}", codigo) for codigo in valores]
        pagamentos = [
            _pagamento(
                f"E-{codigo}", valor, nota=f"P-{codigo}",
                competencia="2026-02" if codigo == "40" else "2025-01",
            )
            for codigo, valor in valores.items()
        ]
        empenhos.append(_empenho("E-CAM", "39"))
        pagamentos.append(_pagamento("E-CAM", 2_400_000, orgao="02", nota="CAM"))
        resultado = _processar(
            [_orgao("01"), _orgao("02", "CAMARA")],
            pagamentos,
            empenhos,
            lambda cnpj, competencia: {"porte_codigo": "05", "temporal_comprovada": True},
        )
        self.assertEqual(resultado.resposta.totais.total_financeiro_centavos, 13_531_152_944)
        self.assertEqual(resultado.resposta.qualidade.pagamentos_processados, 7)
        self.assertEqual(resultado.resposta.qualidade.status.value, "APROVADA")

    def test_retrato_atual_publica_como_parcial(self) -> None:
        resultado = _processar(
            [_orgao("01")],
            [_pagamento("E1", 100)],
            [_empenho("E1", "39")],
            lambda cnpj, competencia: {"porte_padronizado": "ME", "temporal_comprovada": False},
        )
        self.assertEqual(resultado.resposta.qualidade.status.value, "PARCIAL")


if __name__ == "__main__":
    unittest.main()
