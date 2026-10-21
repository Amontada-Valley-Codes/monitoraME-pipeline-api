from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.models import IngestionJob, PncpAvaliacaoQualidade, PncpOportunidade, PncpQuarentena, RawPncpRecord
from app.pncp.normalization import ErroNormalizacaoPncp, normalizar_job, normalizar_payload, normalizar_raw_session
from app.pncp.quality import RULE_VERSION, avaliar_payload, consultar_qualidade_session, estado_sincronizacao, registrar_avaliacao_session


def raw(payload=None, **kwargs):
    dados = {"numeroControlePNCP": "id-1", "orgaoEntidade": {"cnpj": "00394460000141"}, "unidadeOrgao": {"codigoIbge": "0123456"}, "objetoCompra": " Compra ", "valorTotalEstimado": "12345678901234567890.12", "valorTotalHomologado": "0.01", "orcamentoSigiloso": False, "orcamentoSigilosoCodigo": 1, "orcamentoSigilosoDescricao": "Sem sigilo", "dataAberturaProposta": "2026-10-21T10:00:00-03:00", "tipoInstrumentoConvocatorioCodigo": 1,  "dataPublicacaoPncp": "2026-10-21", "dataAtualizacao": "2026-10-21T09:00:00-03:00", "dataEncerramentoProposta": "2026-10-30T10:00:00-03:00", "modalidadeId": 6, "situacaoCompraId": 1}
    if payload: dados.update(payload)
    if not payload or "dataAtualizacaoGlobal" not in payload:
        origem = kwargs.get("source_updated_at") or dados["dataAtualizacao"]
        dados["dataAtualizacaoGlobal"] = origem.isoformat() if isinstance(origem, datetime) else origem
    return RawPncpRecord(id=1, source="PNCP", entity_type="CONTRATACAO", external_id="id-1", collected_at=datetime.now(timezone.utc), source_updated_at=kwargs.pop("source_updated_at", None), payload_hash=kwargs.pop("payload_hash", "hash-1"), payload=dados, run_id="job:7:pagina:1", **kwargs)


class QualidadePncpTest(unittest.TestCase):
    def test_payload_completo_e_precisao_decimal(self):
        resultado = normalizar_payload(raw())
        self.assertEqual(resultado["data_quality_status"], "valid")
        self.assertEqual(resultado["dados_semanticos"]["valor_total_estimado"], "12345678901234567890.12")
        self.assertEqual(resultado["orgao"]["cnpj"], "00394460000141")
        self.assertEqual(resultado["unidade"]["codigoIbge"], "0123456")
        self.assertEqual(resultado["prazo_proposta_final"].hour, 13)
        self.assertEqual(resultado["prazo_proposta_final"].utcoffset().total_seconds(), 0)

    def test_ausente_null_vazio_zero_e_sigilo(self):
        for valor, estado in ((None, "null"), ("", "empty"), ("  ", "empty"), (0, "present")):
            with self.subTest(valor=valor):
                registro = raw({"valorTotalEstimado": valor})
                campo = avaliar_payload(registro)["campos"]["valorTotalEstimado"]
                self.assertEqual(campo["estado_origem"], estado)
                self.assertIsNone(campo["valor"])
        registro = raw()
        del registro.payload["valorTotalEstimado"]
        self.assertEqual(avaliar_payload(registro)["campos"]["valorTotalEstimado"]["estado_origem"], "absent")
        for sigilo in (True, False, None):
            campo = avaliar_payload(raw({"valorTotalEstimado": 0, "orcamentoSigiloso": sigilo}))["campos"]["valorTotalEstimado"]
            self.assertIsNone(campo["valor"])
            self.assertEqual(campo["status"], "warning")

    def test_moeda_invalida_nao_silenciada(self):
        for valor in (True, "NaN", "Infinity", -1, [], "1,00"):
            with self.subTest(valor=valor), self.assertRaises(ErroNormalizacaoPncp):
                normalizar_payload(raw({"valorTotalEstimado": valor}))
        campo = avaliar_payload(raw({"valorTotalEstimado": "1.00001"}))["campos"]["valorTotalEstimado"]
        self.assertEqual(campo["valor"], "1.00001")
        self.assertEqual(campo["status"], "warning")

    def test_cnpj_ibge_e_entidade_invalida(self):
        for payload in ({"orgaoEntidade": {"cnpj": 394460000141}}, {"orgaoEntidade": {"cnpj": "00394460000142"}}, {"unidadeOrgao": {"codigoIbge": 123456}}, {"orgaoEntidade": []}):
            with self.subTest(payload=payload), self.assertRaises(ErroNormalizacaoPncp):
                normalizar_payload(raw(payload))

    def test_data_civil_offset_sem_fuso_e_invalida(self):
        civil = normalizar_payload(raw({"dataEncerramentoProposta": "2026-10-21"}))
        self.assertIsNone(civil["prazo_proposta_final"])
        self.assertEqual(civil["dados_semanticos"]["prazo_proposta_final_origem"]["tipo"], "civil")
        sem_fuso = normalizar_payload(raw({"dataEncerramentoProposta": "2026-10-21T09:00:00"}))
        self.assertEqual(sem_fuso["prazo_proposta_final"].hour, 12)
        self.assertEqual(sem_fuso["dados_semanticos"]["prazo_proposta_final_origem"]["fuso_contrato"], "America/Sao_Paulo")
        for valor in ("2026-02-30", "invalida", "20261021", 0):
            with self.subTest(valor=valor), self.assertRaises(ErroNormalizacaoPncp):
                normalizar_payload(raw({"dataEncerramentoProposta": valor}))
        dado = normalizar_payload(raw())["dados_semanticos"]["data_atualizacao"]
        self.assertEqual(dado["offset_origem"], "-0300")
        self.assertEqual(dado["origem"], "2026-10-21T09:00:00-03:00")

    def test_enum_desconhecido_preservado_com_warning(self):
        registro = raw({"modalidadeId": "999", "situacaoCompraId": 888})
        dado = normalizar_payload(registro)
        self.assertEqual(dado["dados_semanticos"]["modalidade_codigo"], "999")
        self.assertEqual(dado["dados_semanticos"]["situacao_codigo"], 888)
        self.assertEqual(dado["data_quality_status"], "partial")
        session = MagicMock()
        session.get.return_value = None
        with self.assertLogs("app.pncp.quality", level="WARNING"):
            registrar_avaliacao_session(session, registro, avaliar_payload(registro))

    def test_endpoint_e_run_resolvidos_do_coletor_replay_idempotente(self):
        for fluxo in ("publicacao", "atualizacao"):
            with self.subTest(fluxo=fluxo):
                session = MagicMock()
                registro = raw()
                coletor = IngestionJob(scope_key=fluxo + ":6")
                session.get.side_effect = [coletor, None]
                registrar_avaliacao_session(session, registro, avaliar_payload(registro))
                evidencia = session.add.call_args.args[0]
                self.assertEqual(evidencia.endpoint, "/v1/contratacoes/" + fluxo)
                self.assertEqual(evidencia.run_id, "job:7:pagina:1")
                session.get.side_effect = [coletor, evidencia]
                registrar_avaliacao_session(session, registro, avaliar_payload(registro))
                self.assertEqual(evidencia.tentativas, 2)
                self.assertEqual(session.add.call_count, 1)

    def test_relatorio_completude_validade_excecoes_replay(self):
        registro = raw({"modalidadeId": 999})
        evidencia = PncpAvaliacaoQualidade(raw_record_id=1, rule_version=RULE_VERSION, endpoint="/v1/contratacoes/publicacao", run_id="run-1", data_quality_status="partial", campos=avaliar_payload(registro)["campos"], tentativas=2)
        session = MagicMock()
        session.scalars.return_value.all.return_value = [evidencia]
        relatorio = consultar_qualidade_session(session, run_id="run-1", endpoint=evidencia.endpoint)
        self.assertEqual(len(relatorio["metricas"]), 17)
        excecao = relatorio["excecoes"][0]
        self.assertEqual(excecao["raw_id"], 1)
        self.assertTrue(excecao["evidencia_reprocessamento"])
        self.assertEqual(excecao["motivo"], "enum_desconhecido")

    def test_estados_entidade_independentes_dos_jobs(self):
        casos = [(dict(qualidade=None), "pending"), (dict(qualidade="valid", executando=True), "syncing"), (dict(qualidade="valid"), "synchronized"), (dict(qualidade="partial"), "partially_synchronized"), (dict(qualidade="valid", desatualizada=True), "stale"), (dict(qualidade="valid", indisponivel=True), "source_unavailable"), (dict(qualidade="invalid"), "quarantined")]
        for argumentos, esperado in casos:
            self.assertEqual(estado_sincronizacao(**argumentos), esperado)

    @patch("app.pncp.normalization.registrar_avaliacao_session")
    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    def test_upsert_preserva_versao_antiga_empate_e_incompleta(self, eventos, registrar):
        atual = PncpOportunidade(**normalizar_payload(raw(source_updated_at=datetime(2026, 10, 20, tzinfo=timezone.utc))))
        session = MagicMock()
        session.get.return_value = atual
        self.assertEqual(normalizar_raw_session(session, raw()), "INALTERADO")
        self.assertEqual(normalizar_raw_session(session, raw(payload_hash="outro", source_updated_at=datetime(2026, 10, 19, tzinfo=timezone.utc))), "ANTIGO")
        self.assertEqual(normalizar_raw_session(session, raw(payload_hash="outro", source_updated_at=atual.source_updated_at)), "AMBIGUO")
        self.assertEqual(normalizar_raw_session(session, raw({"objetoCompra": None}, payload_hash="outro", source_updated_at=datetime(2026, 10, 21, tzinfo=timezone.utc))), "INCOMPLETO")
        self.assertEqual(atual.payload_hash, "hash-1")
        eventos.assert_not_called()

    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    def test_quarentena_confirmada_antes_da_excecao_e_lote_saudavel(self, eventos):
        session = MagicMock()
        registro = raw({"numeroControlePNCP": None})
        session.get.side_effect = lambda modelo, chave: registro if modelo is RawPncpRecord else None
        confirmados = []
        @contextmanager
        def transacao():
            yield session
            confirmados.extend(session.add.call_args_list)
        job = MagicMock(raw_record_id=1)
        with patch("app.pncp.normalization.orm.main_session", transacao):
            with self.assertRaises(ErroNormalizacaoPncp): normalizar_job(job)
            self.assertTrue(any(isinstance(c.args[0], PncpQuarentena) for c in confirmados))
            registro.payload["numeroControlePNCP"] = "id-1"
            self.assertEqual(normalizar_job(job), "NOVO")
        self.assertTrue(any(isinstance(c.args[0], PncpAvaliacaoQualidade) for c in confirmados))


class ReprocessamentoQualidadeTest(unittest.TestCase):
    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    @patch("app.pncp.normalization.registrar_avaliacao_session")
    def test_mesmo_raw_com_regra_nova_atualiza_sem_apagar_versao(self, registrar, eventos):
        atual = PncpOportunidade(**normalizar_payload(raw()))
        atual.dados_semanticos = {"rule_version": "regra-anterior"}
        session = MagicMock()
        session.get.return_value = atual
        self.assertEqual(normalizar_raw_session(session, raw()), "ALTERADO")
        self.assertEqual(atual.dados_semanticos["rule_version"], RULE_VERSION)
        self.assertEqual(atual.raw_record_id, 1)
        self.assertEqual(registrar.return_value.decisao, "ALTERADO")

    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    @patch("app.pncp.normalization.registrar_avaliacao_session")
    def test_versao_nova_saudavel_substitui_a_anterior(self, registrar, eventos):
        atual = PncpOportunidade(**normalizar_payload(raw(source_updated_at=datetime(2026, 10, 20, tzinfo=timezone.utc))))
        session = MagicMock()
        session.get.return_value = atual
        novo = raw({"objetoCompra": "Objeto atualizado"}, payload_hash="novo", source_updated_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertEqual(normalizar_raw_session(session, novo), "ALTERADO")
        self.assertEqual(atual.objeto, "Objeto atualizado")
        self.assertEqual(atual.payload_hash, "novo")

    def test_espacos_no_prazo_sao_vazio_legitimo(self):
        self.assertIsNone(normalizar_payload(raw({"dataEncerramentoProposta": "  ", "tipoInstrumentoConvocatorioCodigo": 3}))["prazo_proposta_final"])

    def test_relatorio_tem_denominador_raw_nao_so_avaliados(self):
        session = MagicMock()
        session.scalars.return_value.all.return_value = []
        session.execute.return_value.mappings.return_value.all.return_value = [{"raw_total": 10, "avaliados": 7, "cobertura": "0.7", "duplicatas_raw": 0, "quarentena_atual": 2}]
        relatorio = consultar_qualidade_session(session, endpoint="/v1/contratacoes/atualizacao")
        self.assertEqual(relatorio["cobertura_integridade_unicidade"][0]["raw_total"], 10)
        sql = str(session.execute.call_args.args[0])
        self.assertIn("LEFT JOIN pncp_avaliacoes_qualidade", sql)
        self.assertIn("count(DISTINCT", sql)
        self.assertEqual(session.execute.call_args.args[1]["endpoint"], "/v1/contratacoes/atualizacao")

    def test_endpoint_sem_proveniencia_permanece_desconhecido(self):
        registro = raw()
        registro.run_id = None
        session = MagicMock()
        session.get.return_value = None
        registrar_avaliacao_session(session, registro, avaliar_payload(registro))
        self.assertEqual(session.add.call_args.args[0].endpoint, "unknown")


class ContratoOficialQualidadeTest(unittest.TestCase):
    def test_cnpj_alfanumerico_receita_preserva_caracteres_e_verifica_dv(self):
        self.assertEqual(normalizar_payload(raw({"orgaoEntidade": {"cnpj": "12abc34501de35"}}))["orgao"]["cnpj"], "12ABC34501DE35")
        with self.assertRaises(ErroNormalizacaoPncp):
            normalizar_payload(raw({"orgaoEntidade": {"cnpj": "12ABC34501DE34"}}))

    def test_quatro_casas_decimais_sao_permitidas_pelo_pncp(self):
        campo = avaliar_payload(raw({"valorTotalEstimado": "100.0001"}))["campos"]["valorTotalEstimado"]
        self.assertEqual(campo["status"], "valid")
        self.assertEqual(campo["valor"], "100.0001")

    def test_novo_codigo_de_sigilo_nao_perde_semantica(self):
        for codigo in (2, 3, "2", "3"):
            campo = avaliar_payload(raw({"valorTotalEstimado": 0, "orcamentoSigilosoCodigo": codigo}))["campos"]["valorTotalEstimado"]
            self.assertEqual(campo["motivo"], "zero_sigiloso")
            self.assertIsNone(campo["valor"])
        dado = normalizar_payload(raw({"orcamentoSigilosoCodigo": 99}))
        self.assertEqual(dado["dados_semanticos"]["orcamento_sigiloso_codigo"], 99)

    def test_instante_historico_ambiguo_nao_recebe_offset_inventado(self):
        dado = normalizar_payload(raw({"dataEncerramentoProposta": "2018-11-04T00:30:00"}))
        self.assertIsNone(dado["prazo_proposta_final"])
        self.assertEqual(dado["data_quality_status"], "partial")


class VersaoCivilPncpTest(unittest.TestCase):
    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    @patch("app.pncp.normalization.registrar_avaliacao_session")
    def test_data_sem_fuso_ordena_versoes_sem_inventar_instante(self, registrar, eventos):
        atual = PncpOportunidade(**normalizar_payload(raw({"dataAtualizacao": "2026-10-20T09:00:00"})))
        session = MagicMock(); session.get.return_value = atual
        novo = raw({"dataAtualizacao": "2026-10-21T09:00:00", "objetoCompra": "Novo objeto"}, payload_hash="novo")
        self.assertEqual(normalizar_raw_session(session, novo), "ALTERADO")
        self.assertIsNone(atual.source_updated_at)
        antigo = raw({"dataAtualizacao": "2026-10-19T09:00:00"}, payload_hash="antigo")
        self.assertEqual(normalizar_raw_session(session, antigo), "ANTIGO")


class ObrigatoriedadeEAtualizacaoGlobalTest(unittest.TestCase):
    def test_prazos_obrigatorios_apenas_para_instrumentos_conhecidos(self):
        for codigo in (1, 2):
            with self.assertRaises(ErroNormalizacaoPncp):
                normalizar_payload(raw({"tipoInstrumentoConvocatorioCodigo": codigo, "dataEncerramentoProposta": None}))
        self.assertIsNone(normalizar_payload(raw({"tipoInstrumentoConvocatorioCodigo": 3, "dataEncerramentoProposta": None}))["prazo_proposta_final"])

    @patch("app.pncp.normalization.registrar_eventos_alteracao_session")
    @patch("app.pncp.normalization.registrar_avaliacao_session")
    def test_global_atualizado_tem_precedencia_com_data_basica_igual(self, registrar, eventos):
        atual = PncpOportunidade(**normalizar_payload(raw({"dataAtualizacaoGlobal": "2026-10-20T10:00:00"})))
        session = MagicMock(); session.get.return_value = atual
        novo = raw({"dataAtualizacaoGlobal": "2026-10-21T10:00:00"}, payload_hash="novo")
        self.assertEqual(normalizar_raw_session(session, novo), "ALTERADO")
        antigo = raw({"dataAtualizacaoGlobal": "2026-10-19T10:00:00"}, payload_hash="antigo")
        self.assertEqual(normalizar_raw_session(session, antigo), "ANTIGO")


class ConsumoUtcQualidadeTest(unittest.TestCase):
    def test_dto_reconverte_offset_da_sessao_postgresql_para_utc(self):
        from datetime import timedelta
        from app.pncp.opportunities import contrato_consumo
        oportunidade = PncpOportunidade(**normalizar_payload(raw()))
        oportunidade.prazo_proposta_final = datetime(2026, 10, 30, 10, tzinfo=timezone(timedelta(hours=-3)))
        dto = contrato_consumo(oportunidade)
        self.assertEqual(dto["prazo_proposta_final"].hour, 13)
        self.assertEqual(dto["prazo_proposta_final"].utcoffset().total_seconds(), 0)
