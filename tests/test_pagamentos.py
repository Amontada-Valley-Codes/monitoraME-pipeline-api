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
from app.pipeline.ingestion import pagamentos_prefeitura as coleta_pagamentos
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
            ([empenho()], [pagamento(), pagamento(valor_nota_pagamento="1.00")], "Duplicidade conflitante"),
            ([empenho()], [pagamento(codigo_orgao="99")], "sem empenho"),
            ([empenho(codigo_municipio="005")], [], "fora do recorte"),
        ]:
            with self.subTest(erro=erro), self.assertRaisesRegex(ValueError, erro):
                conciliar(es, ps)

    def test_duplicata_identica_de_api_e_contada_apenas_uma_vez(self):
        rows, audit = conciliar([empenho()], [pagamento(), pagamento()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["valor_centavos"], 1_000_000)
        self.assertEqual(audit["excluidos"], {"duplicado_identico": 1})
        self.assertEqual(audit["anomalias"][0]["codigo"], "PAGAMENTO_DUPLICADO_IDENTICO")

    def test_estado_estorno_desconhecido_e_preservado_e_excluido_do_total(self):
        rows, audit = conciliar([empenho()], [pagamento(estado_de_estornado=2)])
        self.assertEqual(rows, [])
        self.assertEqual(audit["excluidos"], {"estado_estorno_desconhecido": 1})
        self.assertEqual(audit["anomalias"][0]["estado_de_estornado"], "2")
        rows, audit = conciliar([empenho()], [pagamento(estado_de_estornado=None)])
        self.assertEqual(rows, [])
        self.assertIsNone(audit["anomalias"][0]["estado_de_estornado"])

    def test_cadastro_de_orgaos_incompleto_rejeitado(self):
        with self.assertRaisesRegex(ValueError, "identificar a Camara"):
            p.conciliar_pagamentos([], [], ORGAOS[1:], codigo_municipio="004", exercicio=2025)

    def test_classificacao_nao_trata_tipo_unidade_como_codigo_universal(self):
        orgaos = [
            {"codigo_orgao": "01", "nome_orgao": "Camara Municipal", "codigo_tipo_unidade": "03"},
            {"codigo_orgao": "02", "nome_orgao": "Secretaria de Educacao", "codigo_tipo_unidade": "02"},
        ]
        es = [empenho(codigo_orgao="01"), empenho(codigo_orgao="02")]
        ps = [pagamento(codigo_orgao="01"), pagamento(codigo_orgao="02")]
        rows, audit = p.conciliar_pagamentos(es, ps, orgaos, codigo_municipio="004", exercicio=2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit["orgaos_camara"], ["01"])
        self.assertEqual(audit["orgaos_executivo_municipal"], ["02"])
        self.assertEqual(audit["classificacoes_orgaos"][0]["regra"], "NOME_ORGAO_NORMALIZADO")

    def test_classificacao_reconhece_variacoes_textuais_do_executivo(self):
        for orgao in [
            {"codigo_orgao": "03", "nome_orgao": "Controlodaria Geral do Municipio e Ouvidoria", "codigo_tipo_unidade": "03"},
            {"codigo_orgao": "10", "nome_orgao": "Sec. de Agronomia, Pesca, Irrigacao e Desenv. Econ. e Rural", "codigo_tipo_unidade": "03"},
        ]:
            with self.subTest(codigo=orgao["codigo_orgao"]):
                classificacao = p.classificar_orgao(orgao)
                self.assertEqual(classificacao["classificacao"], "EXECUTIVO_MUNICIPAL")
                self.assertEqual(classificacao["confianca"], "ALTA")

    def test_documentos_nao_cnpj_preservam_total(self):
        for tipo, doc in [("2", "02530104000133"), ("1", "2530104000133"),
                          ("1", "02530104000134"), ("1", "02530104000133!")]:
            with self.subTest(doc=doc):
                rows, _ = conciliar([empenho(codigo_tipo_negociante=tipo, numero_documento_negociante=doc)], [pagamento()])
                result = p.resumir_pagamentos(rows, {})
                self.assertEqual(result["controle_financeiro"]["por_categoria_centavos"]["NAO_CNPJ"], 1000000)
                self.assertEqual(result["pendencias"][0]["documento_original"], doc)

    def test_classificacao_local_porte_me_inclui_mei_sem_inferencia(self):
        cache = {
            "02530104000133": {
                "opencnpj_status": "ok",
                "dados_normalizados": {
                    "cnpj": "02530104000133",
                    "porte_padronizado": "ME",
                    "optante_mei": True,
                },
            },
        }
        rows, _ = conciliar([empenho()], [pagamento(), pagamento(numero_nota_pagamento="0003")])
        cadastros = p.classificar_fornecedores(rows, cache)
        result = p.resumir_pagamentos(rows, cadastros)
        self.assertEqual(result["total_me_centavos"], 2000000)
        self.assertTrue(result["classificacao_completa"])
        self.assertIsNone(cadastros["02530104000133"]["referencia_base_cadastral"])
        self.assertEqual(result["fornecedores_me"][0]["quantidade_pagamentos"], 2)

    def test_agrega_por_cnpj_e_preserva_razoes_sociais_observadas(self):
        es = [
            empenho(numero_empenho="0001", nome_negociante="Fornecedor Original Ltda"),
            empenho(numero_empenho="0002", nome_negociante="Fornecedor Atualizado Ltda",
                    numero_documento_negociante="02530104000133"),
        ]
        ps = [
            pagamento(numero_empenho="0001", numero_nota_pagamento="0002"),
            pagamento(numero_empenho="0002", numero_nota_pagamento="0003"),
        ]
        rows, _ = conciliar(es, ps)
        result = p.resumir_pagamentos(rows, {
            "02530104000133": {"porte_codigo": "01", "situacao_classificacao": "ok"},
        })
        self.assertEqual(len(result["fornecedores_me"]), 1)
        fornecedor = result["fornecedores_me"][0]
        self.assertEqual(fornecedor["cnpj"], "02530104000133")
        self.assertEqual(fornecedor["valor_pago_centavos"], 2_000_000)
        self.assertEqual(fornecedor["quantidade_pagamentos"], 2)
        self.assertEqual(fornecedor["nomes_observados"], [
            "Fornecedor Atualizado Ltda", "Fornecedor Original Ltda",
        ])

    def test_cadastro_ausente_ou_desconhecido_nao_inventa_porte(self):
        rows, _ = conciliar([empenho()], [pagamento()])
        for porte, status in [("00", "ok"), ("ME", "indisponivel"), (None, None)]:
            with self.subTest(porte=porte, status=status):
                cache = {} if status is None else {
                    "02530104000133": {
                        "opencnpj_status": status,
                        "dados_normalizados": {"porte_padronizado": porte},
                    }
                }
                result = p.resumir_pagamentos(
                    rows,
                    p.classificar_fornecedores(rows, cache),
                )
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

    def test_conferencia_portal_por_elemento_so_no_recorte_referenciado(self):
        es, ps = [], []
        for indice, (elemento, valor) in enumerate(p.REFERENCIA_PORTAL_ACARAU_2025.items()):
            numero = f"{indice + 1:04d}"
            es.append(empenho(numero_empenho=numero, codigo_elemento_despesa=f"3399{elemento}00"))
            ps.append(pagamento(numero_empenho=numero, numero_nota_pagamento=numero,
                                valor_nota_pagamento=f"{valor / 100:.2f}"))
        coleta = {"codigo_municipio": "004", "exercicio": 2025, "referencia_final": "2026-09",
                  "coleta_iniciada_em": "inicio", "coleta_concluida_em": "fim",
                  "orgaos": ORGAOS, "empenhos": es, "pagamentos": ps}
        resultado = p.consultar_pagamentos_prefeitura(
            codigo_municipio="004", exercicio=2025, referencia_final="2026-09",
            consultar_porte=False, coleta=coleta,
        )
        self.assertEqual(resultado["conferencia_portal"]["divergencia_centavos_por_elemento"],
                         {elemento: 0 for elemento in p.REFERENCIA_PORTAL_ACARAU_2025})
        self.assertTrue(resultado["conferencia_portal"]["bate"])
        self.assertFalse(resultado["indicador_me_validado"])
        self.assertEqual(resultado["controle_financeiro"]["total_centavos"], 13_531_152_944)
        ps[0]["valor_nota_pagamento"] = "66318441.20"
        divergente = p.consultar_pagamentos_prefeitura(
            codigo_municipio="004", exercicio=2025, referencia_final="2026-09",
            consultar_porte=False, coleta=coleta,
        )
        self.assertFalse(divergente["conferencia_portal"]["bate"])
        self.assertFalse(divergente["indicador_me_validado"])

    @patch.object(coleta_pagamentos.tce, "buscar_documentos_exercicio")
    def test_coleta_ano_seguinte_exercicio_fixo_e_mes_vazio_nao_interrompe(self, buscar):
        def responder(endpoint, **kwargs):
            if endpoint == "orgaos":
                return ORGAOS
            if endpoint == "unidades_orcamentarias":
                return [
                    {"codigo_orgao": "77", "codigo_unidade_orcamentaria": "01"},
                    {"codigo_orgao": "01", "codigo_unidade_orcamentaria": "01"},
                ]
            return []
        buscar.side_effect = responder
        coleta = coleta_pagamentos.coletar_documentos_prefeitura(
            codigo_municipio="004", exercicio=2025, referencia_final="2026-09",
        )
        result = p.consultar_pagamentos_prefeitura(
            codigo_municipio="004", exercicio=2025, referencia_final="2026-09",
            consultar_porte=False, coleta=coleta,
        )
        calls = buscar.call_args_list
        self.assertEqual(len(calls), 56)
        self.assertTrue(all(c.kwargs["exercicio"] == 2025 for c in calls))
        pagamentos = [c for c in calls if c.args[0] == "notas_pagamentos"]
        self.assertEqual(len(pagamentos), 42)
        self.assertTrue(any(c.kwargs["competencia"] == "2026-09" for c in pagamentos))
        self.assertEqual({c.kwargs["codigo_orgao"] for c in pagamentos}, {"01", "77"})
        self.assertTrue(all("codigo_unidade_orcamentaria" not in c.kwargs for c in pagamentos))
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

if __name__ == "__main__":
    unittest.main()
