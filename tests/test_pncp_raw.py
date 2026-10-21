from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pncp.raw import (
    PayloadPncpInvalido,
    RegistroRawPncp,
    salvar_raw_e_agendar_normalizacao_session,
)


class RawPncpRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registro = RegistroRawPncp(
            entity_type="CONTRATACAO",
            external_id="123",
            payload={"numeroControlePNCP": "123", "objeto": "teste"},
            collected_at=datetime(2026, 10, 20, 12, tzinfo=timezone.utc),
            run_id="run-1",
        )

    def test_nova_versao_cria_raw_e_job_normalizacao(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [17, 31]

        resultado = salvar_raw_e_agendar_normalizacao_session(session, self.registro)

        self.assertTrue(resultado.raw_criado)
        self.assertTrue(resultado.job_criado)
        self.assertEqual(resultado.raw_record_id, 17)
        self.assertEqual(resultado.job_id, 31)
        self.assertEqual(session.scalar.call_count, 2)
        job_stmt = session.scalar.call_args_list[1].args[0]
        self.assertEqual(job_stmt.compile().params["dedupe_key"], "normalize:PNCP:17")

    def test_repeticao_reutiliza_raw_e_job_existentes(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [None, 17, 31]

        resultado = salvar_raw_e_agendar_normalizacao_session(session, self.registro)

        self.assertFalse(resultado.raw_criado)
        self.assertFalse(resultado.job_criado)
        self.assertEqual((resultado.raw_record_id, resultado.job_id), (17, 31))

    def test_hash_independe_da_ordem_das_chaves(self) -> None:
        session_a, session_b = MagicMock(), MagicMock()
        session_a.scalar.side_effect = [1, 2]
        session_b.scalar.side_effect = [3, 4]
        outro = RegistroRawPncp(
            entity_type="CONTRATACAO", external_id="123",
            payload={"objeto": "teste", "numeroControlePNCP": "123"},
            collected_at=self.registro.collected_at,
        )

        primeiro = salvar_raw_e_agendar_normalizacao_session(session_a, self.registro)
        segundo = salvar_raw_e_agendar_normalizacao_session(session_b, outro)

        self.assertEqual(primeiro.payload_hash, segundo.payload_hash)

    def test_payload_invalido_nao_e_enviado_ao_banco(self) -> None:
        session = MagicMock()
        invalido = RegistroRawPncp(
            entity_type="CONTRATACAO", external_id="123", payload={"valor": float("nan")},
            collected_at=self.registro.collected_at,
        )

        with self.assertRaises(PayloadPncpInvalido):
            salvar_raw_e_agendar_normalizacao_session(session, invalido)

        session.scalar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
