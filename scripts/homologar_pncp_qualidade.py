"""Homologa qualidade em PostgreSQL local exclusivo usando envelopes RAW capturados.

Nao coleta, nao faz backfill e nao utiliza o banco de producao. A migration deve
estar aplicada. Falha injetada e controles sinteticos sao identificados no relatorio.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from app.core import orm
from app.core.models import IngestionJob, IngestionJobEvent, PncpEvento, PncpOportunidade, PncpQuarentena, RawPncpRecord
from app.pncp.jobs import NovoJob, WorkerPncp, agendar_job_session, replay_jobs
from app.pncp.opportunities import contrato_consumo
from app.pncp.normalization import normalizar_job, normalizar_raw_session
from app.pncp.quality import RULE_VERSION, avaliar_payload, consultar_qualidade_session
from app.pncp.raw import RegistroRawPncp, salvar_raw_e_agendar_normalizacao_session


def exigir(condicao: bool, descricao: str, verificacoes: list[dict]) -> None:
    verificacoes.append({"criterio": descricao, "aprovado": bool(condicao)})
    if not condicao:
        raise AssertionError(descricao)


def homologar(diretorio_raw: Path) -> dict:
    url = make_url(orm.main_database_url())
    if url.host not in {"127.0.0.1", "localhost"} or not (url.database or "").startswith("pncp_quality_"):
        raise ValueError("Use banco local exclusivo com nome pncp_quality_*; banco da aplicacao nao e permitido")
    verificacoes, fontes, registros, controles = [], [], [], []
    agora = datetime.now(timezone.utc)
    with orm.main_session() as session:
        versao = session.scalar(text("SELECT version_num FROM alembic_version_main"))
        exigir(versao == "0017_pncp_qualidade", "migration aplicada em PostgreSQL real", verificacoes)
        exigir(session.scalar(select(func.count()).select_from(RawPncpRecord)) == 0, "banco exclusivo inicialmente vazio", verificacoes)
    for fluxo in ("publicacao", "atualizacao"):
        arquivo = diretorio_raw / (fluxo + ".json")
        envelope = json.loads(arquivo.read_text(encoding="utf-8"))
        itens = envelope["payload"]["data"]
        exigir(bool(itens) and envelope["status"] == 200, "amostra real nao vazia: " + fluxo, verificacoes)
        with orm.main_session() as session:
            coletor_id, _ = agendar_job_session(session, NovoJob(
                job_type="homologacao", entity_type="CONTRATACAO", source="PNCP", scope_key=fluxo + ":homologacao",
                dedupe_key="homologacao:" + fluxo, priority=0, next_run_at=agora))
            session.get(IngestionJob, coletor_id).status = "SUCCEEDED"
            for item in itens:
                resultado = salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
                    entity_type="CONTRATACAO", external_id=item["numeroControlePNCP"], payload=item,
                    collected_at=datetime.fromisoformat(envelope["collected_at"]), run_id=f"job:{coletor_id}:pagina:1"))
                registros.append({"raw_id": resultado.raw_record_id, "job_id": resultado.job_id, "fluxo": fluxo, "payload_hash": resultado.payload_hash})
        fontes.append({"endpoint": "/v1/contratacoes/" + fluxo, "url": envelope["url"], "capturado_em": envelope["collected_at"],
                       "registros": len(itens), "arquivo": arquivo.name, "sha256_arquivo": hashlib.sha256(arquivo.read_bytes()).hexdigest(), "run_id": f"job:{coletor_id}:pagina:1"})
    # Controle explicitamente sintetico: erro real de contrato, com origem preservada.
    base = deepcopy(itens[0])
    base["numeroControlePNCP"] = "controle-invalido-homologacao"
    base["orgaoEntidade"]["cnpj"] = "00394460000142"
    with orm.main_session() as session:
        controle = salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
            "CONTRATACAO", base["numeroControlePNCP"], base, agora, run_id="controle:contrato-invalido"))
        controles.append({"raw_id": controle.raw_record_id, "natureza": "sintetico", "cenario": "CNPJ com DV invalido"})
    primeiro = registros[0]
    worker = WorkerPncp("homologacao-qualidade", normalizar_job, job_type="normalize", backfill_a_cada=1000)
    avaliar_original = avaliar_payload

    def falha_de_regra(raw):
        avaliacao = avaliar_original(raw)
        if raw.id == primeiro["raw_id"]:
            avaliacao["campos"]["objetoCompra"].update(status="error", motivo="falha_de_regra_injetada")
            avaliacao["data_quality_status"] = "invalid"
        return avaliacao

    # Simula um bug corrigivel de regra; nao altera o payload RAW para obter sucesso.
    with patch("app.pncp.normalization.avaliar_payload", side_effect=falha_de_regra):
        while worker.executar_uma_vez():
            pass
    with orm.main_session() as session:
        exigir(session.get(PncpQuarentena, primeiro["raw_id"]) is not None, "quarentena confirmada apos excecao do worker", verificacoes)
        exigir(session.get(PncpQuarentena, controle.raw_record_id) is not None, "registro invalido isolado", verificacoes)
        exigir(session.scalar(select(func.count()).select_from(PncpOportunidade)) > 0, "lote saudavel processado apesar das falhas", verificacoes)
        hash_antes = session.get(RawPncpRecord, primeiro["raw_id"]).payload_hash
    replays = replay_jobs(job_id=primeiro["job_id"])
    exigir(replays == [primeiro["job_id"]], "replay autorizado usa mecanismo existente", verificacoes)
    while worker.executar_uma_vez():
        pass
    with orm.main_session() as session:
        exigir(session.get(PncpQuarentena, primeiro["raw_id"]) is None, "replay bem-sucedido remove quarentena ativa", verificacoes)
        exigir(session.get(RawPncpRecord, primeiro["raw_id"]).payload_hash == hash_antes, "replay nao altera nem recoleta RAW", verificacoes)
        job = session.get(IngestionJob, primeiro["job_id"])
        exigir(job.status == "SUCCEEDED" and job.replay_count == 1, "job recuperado com auditoria de replay", verificacoes)
        eventos_antes = session.scalar(select(func.count()).select_from(PncpEvento))
        raw_antes = session.scalar(select(func.count()).select_from(RawPncpRecord))
        canonicas_antes = session.scalar(select(func.count()).select_from(PncpOportunidade))
        for raw_id in sorted({r["raw_id"] for r in registros}):
            registro = session.get(RawPncpRecord, raw_id)
            exigir(normalizar_raw_session(session, registro) == "INALTERADO", f"idempotencia RAW {raw_id}", verificacoes)
            repetido = salvar_raw_e_agendar_normalizacao_session(session, RegistroRawPncp(
                registro.entity_type, registro.external_id, registro.payload, registro.collected_at, registro.source_updated_at, registro.run_id))
            exigir(not repetido.raw_criado and repetido.raw_record_id == raw_id, f"deduplicacao RAW {raw_id}", verificacoes)
        session.flush()
        exigir(session.scalar(select(func.count()).select_from(RawPncpRecord)) == raw_antes, "unicidade RAW apos repeticao", verificacoes)
        exigir(session.scalar(select(func.count()).select_from(PncpOportunidade)) == canonicas_antes, "unicidade canonica apos repeticao", verificacoes)
        exigir(session.scalar(select(func.count()).select_from(PncpEvento)) == eventos_antes, "sem evento duplicado no replay", verificacoes)
        try:
            with session.begin_nested():
                session.execute(text("UPDATE pncp_oportunidades SET raw_record_id=-1 WHERE raw_record_id=:raw_id"), {"raw_id": primeiro["raw_id"]})
        except IntegrityError:
            verificacoes.append({"criterio": "FK impede oportunidade sem RAW", "aprovado": True})
        else:
            exigir(False, "FK impede oportunidade sem RAW", verificacoes)
        cnpjs, ibges, moedas, prazos = [], [], [], []
        for raw_id in sorted({r["raw_id"] for r in registros}):
            original = session.get(RawPncpRecord, raw_id)
            oportunidade = session.get(PncpOportunidade, {"source": "PNCP", "external_id": original.external_id})
            cnpjs.append(isinstance(oportunidade.orgao["cnpj"], str) and oportunidade.orgao["cnpj"] == original.payload["orgaoEntidade"]["cnpj"].upper())
            ibges.append(isinstance(oportunidade.unidade["codigoIbge"], str) and oportunidade.unidade["codigoIbge"] == original.payload["unidadeOrgao"]["codigoIbge"])
            estimado = original.payload.get("valorTotalEstimado")
            valor = oportunidade.dados_semanticos["valor_total_estimado"]
            moedas.append(valor is None if estimado == 0 else isinstance(valor, str) and Decimal(valor) == Decimal(str(estimado)))
            origem = oportunidade.dados_semanticos["prazo_proposta_final_origem"]
            dto = contrato_consumo(oportunidade)
            prazos.append(origem["origem"] == original.payload["dataEncerramentoProposta"] and dto["prazo_proposta_final"].utcoffset().total_seconds() == 0)
        exigir(all(cnpjs), "CNPJ string com zeros preservados no PostgreSQL", verificacoes)
        exigir(all(ibges), "IBGE string com zeros preservados no PostgreSQL", verificacoes)
        exigir(all(moedas), "Decimal preservado e zero sem evidencia excluido de gratuidade", verificacoes)
        exigir(all(prazos), "prazos UTC no consumo com origem preservada", verificacoes)
        relatorio = consultar_qualidade_session(session)
        filtrados = {}
        for fonte in fontes:
            filtrado = consultar_qualidade_session(session, run_id=fonte["run_id"], endpoint=fonte["endpoint"])
            exigir(bool(filtrado["metricas"]), "metricas filtraveis: " + fonte["endpoint"], verificacoes)
            exigir(all(m["run_id"] == fonte["run_id"] and m["endpoint"] == fonte["endpoint"] for m in filtrado["metricas"]), "filtros run/endpoint sem vazamento", verificacoes)
            filtrados[fonte["endpoint"]] = len(filtrado["metricas"])
        exigir(all(r["duplicatas_raw"] == 0 and r["sem_entidade_normalizada"] == 0 for r in relatorio["cobertura_integridade_unicidade"]), "unicidade e integridade da amostra", verificacoes)
        exigir(all(r["cobertura"] == 1 for r in relatorio["cobertura_integridade_unicidade"]), "100% dos RAW avaliados", verificacoes)
        relatorio["job_recuperado"] = {"id": job.id, "raw_id": primeiro["raw_id"], "status": job.status, "replay_count": job.replay_count,
            "eventos": [r.event_type for r in session.scalars(select(IngestionJobEvent).where(IngestionJobEvent.job_id == job.id).order_by(IngestionJobEvent.id))]}
        relatorio["totais"] = {"raw": raw_antes, "oportunidades": canonicas_antes, "eventos": eventos_antes, "quarentena_controle": session.scalar(select(func.count()).select_from(PncpQuarentena))}
    return {"resultado": "APROVADO", "natureza": "Homologacao tecnica em PostgreSQL 16 real e isolado; amostras reais mais controle e falha injetada identificados.",
            "rule_version": RULE_VERSION, "executado_em": datetime.now(timezone.utc).isoformat(), "migration": versao,
            "fontes": fontes, "registros": registros, "controles": controles, "verificacoes": verificacoes, "relatorio": relatorio}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diretorio-raw", type=Path, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    resultado = homologar(args.diretorio_raw)
    args.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"resultado": resultado["resultado"], "verificacoes": len(resultado["verificacoes"]), "totais": resultado["relatorio"]["totais"]}))


if __name__ == "__main__":
    main()
