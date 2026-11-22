from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.pncp.ingestion import (
    ClientePncp,
    MODALIDADES_PNCP_PADRAO,
    UFS_NORDESTE,
    agendar_propostas_abertas,
    coletar_propostas_abertas,
)
from app.pncp.jobs import JobReivindicado
from app.pncp.scheduler import ConfiguracaoAgendamentoPncp, _jobs_ciclo


class PropostasAbertasPncpTest(unittest.TestCase):
    def test_cliente_envia_data_modalidade_e_uf_ao_endpoint_de_propostas(self) -> None:
        resposta = MagicMock(status_code=200)
        resposta.json.return_value = {"data": [], "totalPaginas": 0}
        cliente = ClientePncp(session=MagicMock(), protecao=MagicMock())
        cliente.session.get.return_value = resposta

        cliente.buscar_propostas_abertas(
            data_final=datetime(2026, 11, 15).date(),
            modalidade=8,
            uf="CE",
            pagina=1,
            tamanho_pagina=50,
        )

        chamada = cliente.session.get.call_args
        self.assertTrue(chamada.args[0].endswith("/v1/contratacoes/proposta"))
        self.assertEqual(
            chamada.kwargs["params"],
            {"dataFinal": "20261115", "codigoModalidadeContratacao": 8, "uf": "CE", "pagina": 1, "tamanhoPagina": 50},
        )

    def test_agendador_cria_um_job_por_modalidade_e_uf(self) -> None:
        jobs = agendar_propostas_abertas(
            data_final=datetime(2026, 11, 15, tzinfo=timezone.utc),
            modalidades=[6, 8],
            ufs=("CE", "MA"),
        )

        self.assertEqual(len(jobs), 4)
        self.assertTrue(all(job.parameters["flow"] == "propostas_abertas" for job in jobs))
        self.assertTrue(all(job.parameters["dataFinal"] == "2026-11-15" for job in jobs))
        self.assertEqual({job.parameters["uf"] for job in jobs}, {"CE", "MA"})

    def test_ciclo_do_scheduler_agenda_propostas_abertas_por_uf_do_nordeste(self) -> None:
        config = ConfiguracaoAgendamentoPncp()
        jobs = _jobs_ciclo(
            MagicMock(),
            config=config,
            agora=datetime(2026, 11, 15, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(len(jobs), len(MODALIDADES_PNCP_PADRAO) * len(UFS_NORDESTE))
        self.assertTrue(all(job.scope_key.startswith("propostas-abertas:") for job in jobs))
        self.assertEqual({job.parameters["uf"] for job in jobs}, set(UFS_NORDESTE))
        self.assertEqual(
            {job.parameters["codigoModalidadeContratacao"] for job in jobs},
            set(MODALIDADES_PNCP_PADRAO),
        )
        self.assertEqual(
            {job.parameters["dataFinal"] for job in jobs},
            {"2027-12-30"},
        )

    def test_coletor_salva_raw_sem_confirmar_cursor(self) -> None:
        job = JobReivindicado(
            42, "incremental", "CONTRATACAO", "PNCP",
            "propostas-abertas:2026-11-15:uf:CE:modalidade:6:tamanho:50", "dedupe",
            1, 5, "lease", None, None, datetime(2026, 11, 15, tzinfo=timezone.utc), 1,
            {"dataFinal": "2026-11-15", "codigoModalidadeContratacao": 6, "uf": "CE", "tamanhoPagina": 50},
        )
        cliente = MagicMock()
        cliente.buscar_propostas_abertas.return_value = SimpleNamespace(
            itens=[{"numeroControlePNCP": "123"}], total_paginas=1,
        )
        session_context = MagicMock()
        session_context.__enter__.return_value = MagicMock()

        with (
            patch("app.pncp.ingestion.orm.main_session", return_value=session_context),
            patch("app.pncp.ingestion.identificar_contratacao_raw", return_value=("123", "hash")),
            patch("app.pncp.ingestion.salvar_raw_e_agendar_normalizacao_session", return_value=SimpleNamespace(raw_criado=True, job_criado=True)),
            patch("app.pncp.ingestion.confirmar_janela_job") as confirmar_cursor,
        ):
            total = coletar_propostas_abertas(job, cliente)

        self.assertEqual(total, 1)
        cliente.buscar_propostas_abertas.assert_called_once_with(
            data_final=datetime(2026, 11, 15).date(), modalidade=6, uf="CE", pagina=1, tamanho_pagina=50,
        )
        confirmar_cursor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
