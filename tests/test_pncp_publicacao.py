from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp.ingestion import ClientePncp, PncpColetaError, agendar_publicacao, coletar_publicacao
from app.pncp.jobs import JobReivindicado


class PublicacaoPncpTest(unittest.TestCase):
    def test_cliente_usa_parametros_oficiais(self) -> None:
        resposta = MagicMock(status_code=200)
        resposta.json.return_value = {"data": [], "totalPaginas": 1}
        cliente = ClientePncp(session=MagicMock())
        cliente.session.get.return_value = resposta
        cliente.buscar_publicacoes(inicio=datetime(2026, 10, 1).date(), fim=datetime(2026, 10, 2).date(), modalidade=6, pagina=1, tamanho_pagina=50)
        self.assertEqual(cliente.session.get.call_args.kwargs["params"]["dataInicial"], "20261001")
        self.assertEqual(cliente.session.get.call_args.kwargs["params"]["codigoModalidadeContratacao"], 6)

    def test_json_invalido_falha_sem_pagina_vazia(self) -> None:
        resposta = MagicMock(status_code=200); resposta.raise_for_status.return_value = None; resposta.json.side_effect = ValueError("truncado")
        cliente = ClientePncp(session=MagicMock()); cliente.session.get.return_value = resposta
        with self.assertRaises(PncpColetaError):
            cliente.buscar_publicacoes(inicio=datetime.now().date(), fim=datetime.now().date(), modalidade=6, pagina=1, tamanho_pagina=10)

    def test_agenda_uma_janela_por_modalidade(self) -> None:
        inicio = datetime(2026, 10, 1, tzinfo=timezone.utc); fim = datetime(2026, 10, 2, tzinfo=timezone.utc)
        jobs = agendar_publicacao(inicio=inicio, fim=fim, modalidades=[6, 8])
        self.assertEqual(len(jobs), 2); self.assertIn("modalidade:8", jobs[1].scope_key)

    def test_pagina_incompleta_nao_e_sucesso(self) -> None:
        job = JobReivindicado(1, "incremental", "CONTRATACAO", "PNCP", "publicacao:2026-10-01:2026-10-02:modalidade:6:tamanho:100", "d", 1, 5, "t", None, datetime(2026,10,1,tzinfo=timezone.utc), datetime(2026,10,2,tzinfo=timezone.utc), 1)
        cliente = MagicMock(); cliente.buscar_publicacoes.return_value = type("P", (), {"itens": [], "total_paginas": 2})()
        with self.assertRaises(PncpColetaError): coletar_publicacao(job, cliente)

    def test_429_respeita_retry_after_e_deixa_retry_para_o_job(self) -> None:
        resposta = MagicMock(status_code=429)
        resposta.headers = {"Retry-After": "17"}
        cliente = ClientePncp(session=MagicMock())
        cliente.session.get.return_value = resposta

        with self.assertRaises(PncpColetaError) as contexto:
            cliente.buscar_publicacoes(
                inicio=datetime.now().date(), fim=datetime.now().date(), modalidade=6,
                pagina=1, tamanho_pagina=10,
            )

        self.assertTrue(contexto.exception.retryable)
        self.assertEqual(contexto.exception.status_code, 429)
        self.assertEqual(contexto.exception.retry_after.total_seconds(), 17)
        self.assertEqual(cliente.session.get.call_count, 1)

    def test_400_e_permanente(self) -> None:
        resposta = MagicMock(status_code=400)
        resposta.headers = {}
        cliente = ClientePncp(session=MagicMock())
        cliente.session.get.return_value = resposta

        with self.assertRaises(PncpColetaError) as contexto:
            cliente.buscar_publicacoes(
                inicio=datetime.now().date(), fim=datetime.now().date(), modalidade=6,
                pagina=1, tamanho_pagina=10,
            )

        self.assertFalse(contexto.exception.retryable)

