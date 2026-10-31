from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from app.pncp.derived_status import (aplicar_status_session, avaliar_status,
    inferir_status_resultado_itens, reavaliar_status_persistidos)
from app.core.models import PncpOportunidade


class StatusDerivadoTest(unittest.TestCase):
    agora = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    inicio = "2026-10-31T08:00:00-03:00"
    fim = "2026-10-31T15:00:00-03:00"

    def avaliar(self, oficial="Divulgada no PNCP", inicio=None, fim=None, agora=None):
        return avaliar_status(official_status=oficial, inicio=inicio or self.inicio,
            encerramento=fim or self.fim, agora=agora or self.agora)

    def test_janela_e_limite_de_encerramento(self):
        self.assertEqual(self.avaliar().get("derived_status"), "ENCERRANDO_EM_BREVE")
        self.assertEqual(self.avaliar(fim="2026-11-02T12:00:00Z")["derived_status"], "ENCERRANDO_EM_BREVE")
        result = self.avaliar(fim="2026-11-02T12:00:01Z")
        self.assertEqual(result["derived_status"], "ABERTA")
        self.assertEqual(self.avaliar(fim="2026-10-31T12:00:00Z")["derived_status"], "ENCERRADA")

    def test_precedencia_impeditivos_e_resultado(self):
        self.assertEqual(self.avaliar("Suspensa", fim="2026-10-31T18:00:00Z")["derived_status"], "SUSPENSA")
        self.assertEqual(self.avaliar("Resultado homologado parcialmente")["derived_status"], "HOMOLOGADA_PARCIAL")
        self.assertEqual(self.avaliar("Homologada")["derived_status"], "HOMOLOGADA_TOTAL")
        self.assertEqual(self.avaliar("Suspensa e homologada", fim="2026-11-03T12:00:00Z")["derived_status"], "SUSPENSA")
        self.assertEqual(inferir_status_resultado_itens(["PARCIAL", "PARCIAL"]), "RESULTADO_PUBLICADO")
        self.assertEqual(inferir_status_resultado_itens(["FINAL"]), "HOMOLOGADA_TOTAL")
        self.assertEqual(inferir_status_resultado_itens(["FINAL", "PARCIAL"]), "HOMOLOGADA_PARCIAL")

    def test_datas_invalidas_nao_adivinham_e_abertura_futura(self):
        self.assertEqual(avaliar_status(official_status="Divulgada no PNCP", inicio=None,
            encerramento=self.fim, agora=self.agora)["derived_status"], "INDETERMINADO")
        self.assertEqual(self.avaliar(fim="2026-10-31T15:00:00")["derived_status"], "INDETERMINADO")
        self.assertEqual(self.avaliar(inicio="2026-11-01T10:00:00Z", fim="2026-11-02T15:00:00Z")["derived_status"], "ABERTURA_FUTURA")
        self.assertEqual(self.avaliar(inicio="2026-11-01T10:00:00Z", fim="2026-10-31T15:00:00Z")["derived_status"], "INDETERMINADO")

    def test_determinismo(self):
        self.assertEqual(self.avaliar(), self.avaliar())

    def test_evento_somente_para_mudanca_operacional(self):
        session = MagicMock()
        oportunidade = PncpOportunidade(source="PNCP", external_id="id", raw_record_id=1,
            payload_hash="v1", url_oficial="https://pncp.gov.br", situacao="Divulgada no PNCP",
            prazo_proposta_final=datetime(2026, 10, 31, 15, tzinfo=timezone.utc),
            modalidade={}, derived_status="INDETERMINADO", reason_codes=[], data_confidence="BAIXA")
        aplicar_status_session(session, oportunidade, agora=self.agora, inicio=self.inicio)
        self.assertEqual(session.execute.call_count, 1)
        detalhes = session.execute.call_args.args[0].compile().params["details"]
        self.assertEqual(detalhes["before"]["derived_status"], "INDETERMINADO")
        self.assertEqual(detalhes["after"]["derived_status"], "ENCERRANDO_EM_BREVE")
        aplicar_status_session(session, oportunidade, agora=self.agora, inicio=self.inicio)
        self.assertEqual(session.execute.call_count, 1)
        oportunidade.situacao = "Publicada no PNCP"
        aplicar_status_session(session, oportunidade, agora=self.agora, inicio=self.inicio)
        self.assertEqual(session.execute.call_count, 1)

    def test_reavaliacao_temporal_usa_somente_a_visao_local(self):
        oportunidade = PncpOportunidade(source="PNCP", external_id="id", raw_record_id=1,
            payload_hash="v1", url_oficial="https://pncp.gov.br", situacao="Divulgada no PNCP",
            prazo_proposta_final=datetime(2026, 10, 31, 15, tzinfo=timezone.utc),
            modalidade={}, dados_semanticos={
                "data_abertura_proposta": "2026-10-31T12:00:00Z",
                "prazo_proposta_final_origem": "2026-10-31T15:00:00Z"},
            derived_status="INDETERMINADO", reason_codes=[], data_confidence="BAIXA")
        session = MagicMock()
        session.execute.return_value = []
        session.scalars.return_value.yield_per.return_value = [oportunidade]
        manager = MagicMock()
        manager.__enter__.return_value = session
        manager.__exit__.return_value = False
        with patch("app.pncp.derived_status.orm.main_session", return_value=manager):
            self.assertEqual(reavaliar_status_persistidos(agora=datetime(2026, 10, 31, 11, tzinfo=timezone.utc)), 1)
            self.assertEqual(oportunidade.derived_status, "ABERTURA_FUTURA")
            self.assertEqual(reavaliar_status_persistidos(agora=datetime(2026, 10, 31, 12, tzinfo=timezone.utc)), 1)
        self.assertEqual(oportunidade.derived_status, "ENCERRANDO_EM_BREVE")


if __name__ == "__main__":
    unittest.main()
