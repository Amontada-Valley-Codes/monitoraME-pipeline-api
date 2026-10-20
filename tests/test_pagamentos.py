from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key, value in {
    "DATABASE_URL": "postgresql://postgres:postgres@localhost/test",
    "LOG_DATABASE_URL": "postgresql://postgres:postgres@localhost/logs",
    "TCE_CE_BASE_URL": "https://api-dados-abertos.tce.ce.gov.br/sim",
    "OPENCNPJ_BASE_URL": "https://api.opencnpj.org",
    "IBGE_LOCALIDADES_BASE_URL": "https://servicodados.ibge.gov.br/api/v1/localidades",
    "UF_PADRAO": "CE", "CODIGO_MUNICIPIO_TCE_PADRAO": "004",
}.items():
    os.environ.setdefault(key, value)

from app.pipeline import pagamentos as p
from app.pipeline.ingestion import tce
from app.pipeline.cleaners.opencnpj import normalizar_porte_empresarial

ORGAOS = [
    {"codigo_orgao": "77", "nome_orgao": "Camara Municipal", "codigo_tipo_unidade": "02"},
    {"codigo_orgao": "01", "nome_orgao": "Secretaria de Saude", "codigo_tipo_unidade": "03"},
]


def empenho(**kwargs):
    return {
        "codigo_municipio": "004", "exercicio_orcamento": 202500,
        "codigo_orgao": "01", "codigo_unidade_orcamentaria": "01  ",
        "data_emissao_empenho": "2025-01-15T00:00:00", "numero_empenho": "0001",
        "codigo_elemento_despesa": "33903500", "codigo_tipo_negociante": "1",
        "numero_documento_negociante": "02.530.104/0001-33",
        "nome_negociante": "Fornecedor", "valor_empenhado": "9999999.99",
        "data_referencia_doc": 202501, **kwargs,
    }


def pagamento(**kwargs):
    return {**empenho(), "numero_sub_empenho": "001", "numero_nota_pagamento": "0002",
            "codigo_unidade_orcamentaria": "01", "data_emissao_empenho": "20250115",
            "data_referencia_doc": 202602, "data_nota_pagamento": "2026-02-02T00:00:00",
            "estado_de_estornado": 0, "valor_nota_pagamento": "10000.00", **kwargs}


def conciliar(es, ps):
    return p.conciliar_pagamentos(es, ps, ORGAOS, codigo_municipio="004", exercicio=2025)


class PagamentosTest(unittest.TestCase):
    def test_cruza_chave_normalizada_sem_igualdade_de_referencia(self):
        rows, audit = conciliar([empenho()], [pagamento()])
        self.assertEqual(rows[0]["valor_centavos"], 1000000)
        self.assertEqual(rows[0]["cnpj"], "02530104000133")
        self.assertEqual(rows[0]["data_pagamento"], "2026-02-02")
        self.assertEqual(audit["pagamentos_sem_empenho"], 0)

    def test_filtros_financeiros_e_elemento_nas_posicoes_corretas(self):
        es = [empenho(), empenho(codigo_orgao="77"),
              empenho(numero_empenho="0003", codigo_elemento_despesa="35303600")]
        ps = [pagamento(), pagamento(codigo_orgao="77"),
              pagamento(numero_nota_pagamento="0003", estado_de_estornado=1),
              pagamento(numero_empenho="0003")]
        rows, audit = conciliar(es, ps)
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit["excluidos"], {"camara": 1, "estornado": 1, "fora_naturezas": 1})
        self.assertEqual(audit["orgaos_camara"], ["77"])

    def test_pagamentos_parciais_somam_notas_uma_vez(self):
        rows, _ = conciliar([empenho()], [pagamento(valor_nota_pagamento="0.10"),
            pagamento(numero_nota_pagamento="0003", valor_nota_pagamento="0.20")])
        result = p.resumir_pagamentos(rows, {})
        self.assertEqual(result["controle_financeiro"]["total_centavos"], 30)
        self.assertEqual(result["total_me_centavos"], 0)
        self.assertFalse(result["classificacao_completa"])

    def test_duplicatas_e_orfaos_nao_produzem_total(self):
        for es, ps, erro in [
            ([empenho(), empenho()], [pagamento()], "empenho duplicada"),
            ([empenho()], [pagamento(), pagamento()], "pagamento duplicada"),
            ([empenho()], [pagamento(codigo_orgao="99")], "sem empenho"),
            ([empenho()], [pagamento(estado_de_estornado=None)], "obrigatorio"),
            ([empenho()], [pagamento(estado_de_estornado=2)], "desconhecido"),
            ([empenho(codigo_municipio="005")], [], "fora do recorte"),
        ]:
            with self.subTest(erro=erro), self.assertRaisesRegex(ValueError, erro):
                conciliar(es, ps)

    def test_cadastro_de_orgaos_incompleto_rejeitado(self):
        with self.assertRaisesRegex(ValueError, "identificar a Camara"):
            p.conciliar_pagamentos([], [], ORGAOS[1:], codigo_municipio="004", exercicio=2025)

    def test_documentos_nao_cnpj_preservam_total(self):
        for tipo, doc in [("2", "02530104000133"), ("1", "2530104000133"),
                          ("1", "02530104000134"), ("1", "02530104000133!")]:
            with self.subTest(doc=doc):
                rows, _ = conciliar([empenho(codigo_tipo_negociante=tipo, numero_documento_negociante=doc)], [pagamento()])
                result = p.resumir_pagamentos(rows, {})
                self.assertEqual(result["controle_financeiro"]["por_categoria_centavos"]["NAO_CNPJ"], 1000000)
                self.assertEqual(result["pendencias"][0]["documento_original"], doc)

    @patch.object(p.fornecedores, "coletar_fornecedor")
    def test_consulta_unica_porte_me_inclui_mei_sem_inferencia(self, coletar):
        coletar.return_value = {
            "porte": "01", "opencnpj_status": "ok",
            "opencnpj": {"cnpj": "02530104000133", "opcao_mei": "S"},
        }
        rows, _ = conciliar([empenho()], [pagamento(), pagamento(numero_nota_pagamento="0003")])
        cadastros = p.classificar_fornecedores(rows)
        result = p.resumir_pagamentos(rows, cadastros)
        coletar.assert_called_once_with("02530104000133")
        self.assertEqual(result["total_me_centavos"], 2000000)
        self.assertTrue(result["classificacao_completa"])
        self.assertIsNone(cadastros["02530104000133"]["referencia_base_cadastral"])
        self.assertEqual(result["fornecedores_me"][0]["quantidade_pagamentos"], 2)

    @patch.object(p.fornecedores, "coletar_fornecedor")
    def test_cadastro_divergente_ou_desconhecido_nao_inventa_porte(self, coletar):
        rows, _ = conciliar([empenho()], [pagamento()])
        for porte, doc, status in [("ME", "17016110000144", "ok"), ("00", "02530104000133", "ok"),
                                    ("ME", "02530104000133", "indisponivel")]:
            with self.subTest(porte=porte, doc=doc):
                coletar.return_value = {"porte": porte, "opencnpj_status": status, "opencnpj": {"cnpj": doc}}
                result = p.resumir_pagamentos(rows, p.classificar_fornecedores(rows))
                self.assertEqual(result["total_me_centavos"], 0)
                self.assertEqual(result["controle_financeiro"]["total_centavos"], 1000000)
                self.assertFalse(result["classificacao_completa"])

    def test_consultoria_referencia_com_classificacao_hipotetica(self):
        valores = ["761880.06", "120000.00", "201400.65"]
        cnpjs = ["02530104000133", "17016110000144", "26940943000198"]
        es = [empenho(numero_empenho=str(i), numero_documento_negociante=cnpj) for i, cnpj in enumerate(cnpjs)]
        ps = [pagamento(numero_empenho=str(i), valor_nota_pagamento=valor) for i, valor in enumerate(valores)]
        rows, _ = conciliar(es, ps)
        classificacoes = {cnpj: {"porte_codigo": "05" if i == 2 else "01", "situacao_classificacao": "ok"}
                          for i, cnpj in enumerate(cnpjs)}
        result = p.resumir_pagamentos(rows, classificacoes)
        self.assertEqual(result["controle_financeiro"]["total_centavos"], 108328071)
        self.assertEqual(result["total_me_centavos"], 88188006)
        self.assertEqual(sum(result["controle_financeiro"]["por_categoria_centavos"].values()), 108328071)

    @patch.object(p.tce, "buscar_documentos_exercicio")
    def test_coleta_ano_seguinte_exercicio_fixo_e_mes_vazio_nao_interrompe(self, buscar):
        buscar.side_effect = lambda endpoint, **kw: ORGAOS if endpoint == "orgaos" else []
        result = p.consultar_pagamentos_prefeitura(codigo_municipio="004", exercicio=2025, referencia_final="2026-09", consultar_porte=False)
        calls = buscar.call_args_list
        self.assertEqual(len(calls), 34)
        self.assertTrue(all(c.kwargs["exercicio"] == 2025 for c in calls))
        self.assertEqual(calls[-1].kwargs["competencia"], "2026-09")
        self.assertEqual(result["referencias_pagamentos"], ["2025-01", "2026-09"])
        self.assertEqual(p._referencias("2025-12", "2026-01"), ["2025-12", "2026-01"])
        with self.assertRaises(ValueError):
            p._referencias("2025-01", "2025-13")

    @patch.object(tce, "buscar_dados_tce")
    def test_paginacao_reinicia_por_mes_e_preserva_exercicio(self, buscar):
        buscar.side_effect = [{"elements": [{"id": i} for i in range(1000)]}, {"elements": []}, {"elements": []}]
        with patch("app.pipeline.ingestion.pagination.time.sleep"):
            for competencia in ["2025-12", "2026-01"]:
                tce.buscar_documentos_exercicio("notas_pagamentos", codigo_municipio="004", exercicio=2025, competencia=competencia)
        self.assertEqual([c.args[1]["$start_index"] for c in buscar.call_args_list], [0, 1000, 0])
        self.assertTrue(all(c.args[1]["exercicio_orcamento"] == 202500 for c in buscar.call_args_list))

    @patch.object(tce, "buscar_dados_tce")
    def test_resposta_invalida_nao_significa_mes_vazio(self, buscar):
        for resposta in [{}, {"elements": {}}, {"elements": ["erro"]}]:
            buscar.return_value = resposta
            with self.assertRaises(tce.TceIndisponivelError):
                tce.listar_registros("notas_pagamentos", {}, falhar_ao_esgotar=True)

    def test_codigos_receita_normalizados_sem_classificar_codigo_desconhecido(self):
        for entrada, saida in [("01", "ME"), (1, "ME"), ("03", "EPP"), ("05", "DEMAIS"), ("00", None), (2, None)]:
            self.assertEqual(normalizar_porte_empresarial(entrada), saida)

    def test_rota_registrada_e_traduz_erro_financeiro(self):
        from app.api.endpoints.pipeline import router, tce_pagamentos_overview
        from fastapi import HTTPException
        self.assertIn("/pipeline/tce/pagamentos/overview", [r.path for r in router.routes])
        with patch.object(p, "consultar_pagamentos_prefeitura", side_effect=ValueError("duplicada")):
            with self.assertRaises(HTTPException) as caught:
                tce_pagamentos_overview("004", 2025, "2026-09")
            self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
