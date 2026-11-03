from __future__ import annotations

import unittest
from unittest.mock import patch

from requests.structures import CaseInsensitiveDict
from sqlalchemy import create_engine, text

from app.core import orm
from scripts.carga_indicadores import (
    CacheAquecidoIndisponivelError,
    Cenario,
    ResultadoRequisicao,
    _estado_cache,
    _metricas_resultados,
    _tempo_banco,
    executar_carga,
    montar_cenarios,
    montar_relatorio,
    percentil,
    renderizar_markdown,
)


def _resultado(
    *,
    municipio: str,
    latencia: float,
    status: int | None = 200,
    banco: float | None = 10,
) -> ResultadoRequisicao:
    return ResultadoRequisicao(
        cenario="distribuicoes_gerais",
        municipio=municipio,
        status_code=status,
        tempo_resposta_ms=latencia,
        tempo_banco_ms=banco,
        consultas_banco=2 if banco is not None else None,
        cache="MISS",
        erro=None if status == 200 else "falha",
    )


class CargaIndicadoresTest(unittest.TestCase):
    def test_percentil_usa_nearest_rank(self) -> None:
        valores = [100, 200, 300, 400, 500]

        self.assertEqual(percentil(valores, 0.50), 300)
        self.assertEqual(percentil(valores, 0.95), 500)
        self.assertEqual(percentil([], 0.95), 0)

    def test_cenarios_combinam_multiplos_municipios_e_filtros(self) -> None:
        cenarios = montar_cenarios(
            ["010", "057"],
            data_inicial="2025-01-01",
            data_final="2025-12-31",
        )

        self.assertEqual(len(cenarios), 10)
        self.assertEqual({item.municipio for item in cenarios}, {"010", "057"})
        self.assertIn("compras_locais", {item.nome for item in cenarios})
        self.assertIn("distribuicao_mei", {item.nome for item in cenarios})
        elementos = next(
            item for item in cenarios if item.nome == "distribuicao_elementos_despesa"
        )
        self.assertEqual(
            sum(chave == "expense_element_codes" for chave, _ in elementos.parametros),
            7,
        )

    def test_metricas_incluem_p95_p99_throughput_erros_e_banco(self) -> None:
        resultados = [
            _resultado(municipio="010", latencia=100),
            _resultado(municipio="057", latencia=200),
            _resultado(municipio="010", latencia=300, status=500, banco=None),
        ]

        metricas = _metricas_resultados(resultados, duracao_lote_segundos=1.5)

        self.assertEqual(metricas["tempo_resposta_ms"]["media"], 200)
        self.assertEqual(metricas["tempo_resposta_ms"]["p95"], 300)
        self.assertEqual(metricas["tempo_resposta_ms"]["p99"], 300)
        self.assertEqual(metricas["throughput_requisicoes_segundo"], 2)
        self.assertEqual(metricas["taxa_erro_percentual"], 33.333)
        self.assertEqual(metricas["banco"]["amostras"], 2)
        self.assertEqual(metricas["banco"]["consultas_total"], 4)

    def test_extrai_tempo_do_banco_do_server_timing(self) -> None:
        headers = CaseInsensitiveDict(
            {"Server-Timing": 'app;dur=25.1, db;dur=12.345;desc="PostgreSQL"'}
        )

        self.assertEqual(_tempo_banco(headers), 12.345)

    def test_normaliza_header_de_cache_duplicado(self) -> None:
        headers = CaseInsensitiveDict({"X-Cache": "MISS, MISS"})

        self.assertEqual(_estado_cache(headers), "MISS")

    def test_relatorio_documenta_aceite_e_markdown(self) -> None:
        resultados = [
            _resultado(municipio="010", latencia=100),
            _resultado(municipio="057", latencia=200),
        ]
        relatorio = montar_relatorio(
            resultados=resultados,
            duracao_lote_segundos=1,
            recursos={},
            base_url="http://localhost:8000",
            municipios=["010", "057"],
            data_inicial="2025-01-01",
            data_final="2025-12-31",
            requisicoes=2,
            concorrencia=2,
            modo_cache="bypass",
            p95_alvo_ms=1500,
            erro_maximo_percentual=0,
        )

        self.assertTrue(
            relatorio["criterios_aceite"]["multiplos_municipios"]["atendido"]
        )
        self.assertTrue(
            relatorio["criterios_aceite"]["meta_operacional"]["atendido"]
        )
        markdown = renderizar_markdown(relatorio)
        self.assertIn("| p95 | 200.000 ms |", markdown)
        self.assertIn("Múltiplos municípios: ATENDIDO", markdown)

    @patch("scripts.carga_indicadores.executar_requisicao")
    def test_modo_warm_falha_se_cache_nao_produzir_hit(self, requisicao) -> None:
        requisicao.return_value = _resultado(municipio="010", latencia=100)
        cenario = Cenario(
            nome="distribuicoes_gerais",
            municipio="010",
            parametros=(),
        )

        with self.assertRaises(CacheAquecidoIndisponivelError):
            executar_carga(
                base_url="http://localhost:8000",
                cenarios=[cenario],
                requisicoes=1,
                concorrencia=1,
                timeout_segundos=1,
                modo_cache="warm",
                aquecimento_por_cenario=1,
                headers_adicionais={},
                pids_monitorados={},
            )


class MedicaoBancoTest(unittest.TestCase):
    def test_instrumentacao_soma_consultas_apenas_dentro_da_medicao(self) -> None:
        engine = create_engine("sqlite://")
        orm._registrar_medicao_banco(engine)
        token = orm.iniciar_medicao_banco()
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                connection.execute(text("SELECT 2"))
            medicao = orm.obter_medicao_banco()
        finally:
            orm.finalizar_medicao_banco(token)
            engine.dispose()

        self.assertEqual(medicao["queries"], 2)
        self.assertGreaterEqual(medicao["duration_ms"], 0)
        self.assertEqual(orm.obter_medicao_banco()["queries"], 0)


if __name__ == "__main__":
    unittest.main()
