"""Mede Top-K HNSW contra referência exata em dataset PNCP julgado."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core import orm
from app.nlp.embeddings.generator import gerar_embeddings_perfis
from app.nlp.embeddings.model import E5EmbeddingModel
from app.nlp.matching.postgres_retrieval import ConsultaCnae, buscar_contratacoes_top_k


def _metricas(ranking: list[str], julgamentos: dict[str, int], ks: list[int]) -> dict[str, float | int]:
    relevantes = {procurement_id for procurement_id, nivel in julgamentos.items() if nivel >= 2}
    metricas: dict[str, float | int] = {"relevant": len(relevantes)}
    for k in ks:
        top = ranking[:k]
        metricas[f"precision@{k}"] = sum(item in relevantes for item in top) / k
        metricas[f"recall@{k}"] = (
            sum(item in relevantes for item in top) / len(relevantes) if relevantes else 0.0
        )
    return metricas


def _percentil(valores: list[float], percentual: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, math.ceil(percentual * len(ordenados)) - 1)
    return ordenados[indice]


def _consulta_vetorial(model: E5EmbeddingModel, query: dict[str, Any]) -> list[ConsultaCnae]:
    parts = query.get("profile_parts") or [{"code": "profile", "text": query["profile_text"]}]
    encoded = gerar_embeddings_perfis(model, [part["text"] for part in parts])
    return [
        ConsultaCnae(str(part.get("code", "profile")), vector)
        for part, vector in zip(parts, encoded.vectors)
    ]


def avaliar(dataset: dict[str, Any], *, k: int) -> dict[str, Any]:
    process = psutil.Process()
    rss_before = process.memory_info().rss / (1024 * 1024)
    cpu_before = process.cpu_times().user + process.cpu_times().system
    model = E5EmbeddingModel(batch_size=16)
    model_load_start = perf_counter()
    model.load()
    model_load_seconds = perf_counter() - model_load_start
    rss_after_model = process.memory_info().rss / (1024 * 1024)
    docs = dataset["documents"]
    procurement_ids = sorted({doc["procurement_id"].split("|", 1)[-1] for doc in docs})
    ks = [value for value in (5, 10, 20) if value <= k]
    output: dict[str, Any] = {
        "dataset_version": dataset.get("dataset_version"),
        "judgment_status": dataset.get("judgment_status"),
        "model_version": model.model_version,
        "k": k,
        "judged_opportunities": len(procurement_ids),
        "queries": {},
    }
    latencies: dict[str, list[float]] = {"ann": [], "exact": []}
    profile_encoding_seconds = 0.0

    for profile in dataset["queries"]:
        encoding_start = perf_counter()
        consultas = _consulta_vetorial(model, profile)
        profile_encoding_seconds += perf_counter() - encoding_start
        rankings: dict[str, list[str]] = {}
        for search_type, exact, overfetch in (("ann", False, 5), ("exact", True, 10000)):
            start = perf_counter()
            with orm.main_session() as session:
                results = buscar_contratacoes_top_k(
                    session,
                    consultas,
                    model_version=model.model_version,
                    k=k,
                    procurement_ids=procurement_ids,
                    overfetch=overfetch,
                    exact=exact,
                )
            latencies[search_type].append(perf_counter() - start)
            rankings[search_type] = [
                f"{item.source}|{item.procurement_id}" for item in results
            ]
        output["queries"][profile["query_id"]] = {
            "cnaes": [item.codigo for item in consultas],
            "ann": _metricas(rankings["ann"], profile["judgments"], ks),
            "exact": _metricas(rankings["exact"], profile["judgments"], ks),
            "ann_recall_of_exact@k": (
                len(set(rankings["ann"][:k]) & set(rankings["exact"][:k]))
                / max(1, len(rankings["exact"][:k]))
            ),
        }

    with orm.main_session() as session:
        extension = session.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        index = session.scalar(text("""
            SELECT pg_size_pretty(pg_relation_size('idx_nlp_procurement_embeddings_hnsw'))
            WHERE to_regclass('idx_nlp_procurement_embeddings_hnsw') IS NOT NULL
        """))
        vectors = session.scalar(text("SELECT count(*) FROM nlp_procurement_embeddings"))

    output["runtime"] = {
        "hnsw_extension_version": extension,
        "hnsw_index_size": index,
        "stored_vectors": vectors,
        "ann_latency_ms_p50": round(statistics.median(latencies["ann"]) * 1000, 2) if latencies["ann"] else 0,
        "ann_latency_ms_p95": round(_percentil(latencies["ann"], 0.95) * 1000, 2),
        "exact_latency_ms_p50": round(statistics.median(latencies["exact"]) * 1000, 2) if latencies["exact"] else 0,
        "exact_latency_ms_p95": round(_percentil(latencies["exact"], 0.95) * 1000, 2),
        "profile_encoding_seconds": round(profile_encoding_seconds, 3),
        "model_load_seconds": round(model_load_seconds, 3),
        "process_cpu_seconds": round(
            process.cpu_times().user + process.cpu_times().system - cpu_before, 3
        ),
        "process_rss_before_mb": round(rss_before, 1),
        "process_rss_after_model_mb": round(rss_after_model, 1),
        "process_rss_after_mb": round(process.memory_info().rss / (1024 * 1024), 1),
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k deve ser maior que zero")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    resultado = avaliar(dataset, k=args.k)
    rendered = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
