"""Compara E5 com TF-IDF lexical em pares PNCP julgados."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import math
import re
import sys
from threading import Event, Thread
from pathlib import Path
from time import perf_counter, process_time
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import orm
from app.core.models import NlpProcurementSegment
from app.nlp.embeddings.model import E5EmbeddingModel


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)


def _features(text: str) -> Counter[str]:
    tokens = _tokens(text)
    features = Counter(f"w:{token}" for token in tokens)
    compact = f"  {' '.join(tokens)}  "
    features.update(f"c:{compact[i:i + 3]}" for i in range(max(0, len(compact) - 2)))
    return features


def _tfidf(texts: list[str]) -> list[dict[str, float]]:
    raw = [_features(text) for text in texts]
    frequencia_documental: Counter[str] = Counter()
    for features in raw:
        frequencia_documental.update(features.keys())
    total = max(1, len(texts))
    vectors: list[dict[str, float]] = []
    for features in raw:
        vector = {
            token: count * (math.log((1 + total) / (1 + frequencia_documental[token])) + 1)
            for token, count in features.items()
        }
        norma = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({token: value / norma for token, value in vector.items()})
    return vectors


def _cosine(esquerda: dict[str, float], direita: dict[str, float]) -> float:
    if len(esquerda) > len(direita):
        esquerda, direita = direita, esquerda
    return sum(value * direita.get(token, 0.0) for token, value in esquerda.items())


def _rank_segmentos(scores: list[float], documentos: list[dict[str, Any]]) -> dict[str, float]:
    por_oportunidade: dict[str, float] = {}
    for score, documento in zip(scores, documentos):
        chave = documento["procurement_id"]
        por_oportunidade[chave] = max(score, por_oportunidade.get(chave, -1.0))
    return por_oportunidade


def _grupos_de_partes(queries: list[dict[str, Any]]) -> tuple[list[str], list[list[int]], list[list[int]]]:
    textos: list[str] = []
    todos: list[list[int]] = []
    principais: list[list[int]] = []
    for query in queries:
        partes = query.get("profile_parts") or [{"text": query["profile_text"], "is_primary": True}]
        indices: list[int] = []
        indices_principais: list[int] = []
        for parte in partes:
            indice = len(textos)
            textos.append(parte["text"])
            indices.append(indice)
            if parte.get("is_primary"):
                indices_principais.append(indice)
        if not indices:
            raise ValueError("cada perfil precisa de ao menos uma parte CNAE")
        todos.append(indices)
        principais.append(indices_principais or indices[:1])
    return textos, todos, principais


def _max_scores_por_grupo(scores: np.ndarray, grupos: list[list[int]]) -> list[list[float]]:
    return [np.max(scores[indices], axis=0).astype(float).tolist() for indices in grupos]


def _metricas(ranking: dict[str, float], julgamentos: dict[str, int], ks: list[int]) -> dict[str, Any]:
    ordenados = sorted(ranking, key=lambda item: (-ranking[item], item))
    relevantes = {key for key, valor in julgamentos.items() if valor >= 2}
    resultado: dict[str, Any] = {"relevant_opportunities": len(relevantes)}
    for k in ks:
        primeiros = ordenados[:k]
        acertos = sum(item in relevantes for item in primeiros)
        resultado[f"precision@{k}"] = acertos / k
        resultado[f"recall@{k}"] = acertos / len(relevantes) if relevantes else 0.0
    dcg = sum((2 ** max(0, julgamentos.get(item, 0)) - 1) / math.log2(pos + 2)
              for pos, item in enumerate(ordenados[:10]))
    ideal = sorted(julgamentos.values(), reverse=True)[:10]
    idcg = sum((2 ** valor - 1) / math.log2(pos + 2) for pos, valor in enumerate(ideal))
    resultado["ndcg@10"] = dcg / idcg if idcg else 0.0
    resultado["top10"] = [
        {"procurement_id": item, "score": round(ranking[item], 6),
         "relevance": julgamentos[item]}
        for item in ordenados[:10]
    ]
    return resultado


def _validar(dataset: dict[str, Any]) -> None:
    if not dataset.get("dataset_version"):
        raise ValueError("dataset_version é obrigatório")
    queries = dataset.get("queries")
    documents = dataset.get("documents")
    if not isinstance(queries, list) or not queries or not isinstance(documents, list) or not documents:
        raise ValueError("queries e documents devem ser listas não vazias")
    ids = {item.get("procurement_id") for item in documents}
    if None in ids:
        raise ValueError("cada documento precisa de procurement_id")
    for document in documents:
        if not isinstance(document.get("text"), str) or not document["text"].strip():
            raise ValueError("cada segmento precisa de text não vazio")
    for query in queries:
        if not query.get("query_id") or not isinstance(query.get("profile_text"), str):
            raise ValueError("cada query precisa de query_id e profile_text")
        partes = query.get("profile_parts")
        if partes is not None and (
            not isinstance(partes, list)
            or not partes
            or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) or not p["text"].strip()
                   for p in partes)
        ):
            raise ValueError("profile_parts deve conter partes CNAE com text não vazio")
        judgments = query.get("judgments")
        if not isinstance(judgments, dict) or set(judgments) != ids:
            raise ValueError("julgue todas as oportunidades candidatas para cada query")
        if any(value not in (0, 1, 2) for value in judgments.values()):
            raise ValueError("julgamentos devem ser 0=irrelevante, 1=incerto ou 2=relevante")


def _preparar_amostra_banco(
    companies_path: Path, limite: int, limite_empresas: int
) -> dict[str, Any]:
    if not 1 <= limite <= 1000:
        raise ValueError("--limit deve estar entre 1 e 1000 editais")
    if not 1 <= limite_empresas <= 500:
        raise ValueError("--companies-limit deve estar entre 1 e 500 perfis")
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    if not isinstance(companies, list) or not companies:
        raise ValueError("companies.json deve conter uma lista de perfis")
    # Favorece diversidade setorial pelo grupo de dois dígitos do CNAE
    # principal e preenche vagas restantes na ordem original da fixture.
    selecionadas: list[dict[str, Any]] = []
    grupos: set[str] = set()
    restantes: list[dict[str, Any]] = []
    for company in companies:
        grupo = str(company.get("primary_cnae", ""))[:2]
        if grupo and grupo not in grupos and len(selecionadas) < limite_empresas:
            selecionadas.append(company)
            grupos.add(grupo)
        else:
            restantes.append(company)
    companies = (selecionadas + restantes)[:limite_empresas]
    try:
        import sqlalchemy as sa
    except ImportError as exc:
        raise RuntimeError("SQLAlchemy não está instalado no ambiente") from exc

    with orm.main_session() as session:
        oportunidades = session.execute(
            sa.select(NlpProcurementSegment.source, NlpProcurementSegment.external_id)
            .where(
                NlpProcurementSegment.source == "PNCP",
                NlpProcurementSegment.status == "READY",
                NlpProcurementSegment.normalized_text.is_not(None),
            )
            .group_by(NlpProcurementSegment.source, NlpProcurementSegment.external_id)
            .order_by(sa.func.random())
            .limit(limite)
        ).all()
        if not oportunidades:
            raise ValueError(
                "nenhum segmento READY encontrado; sincronize antes com "
                "python -m app.nlp.cli sync-corpus"
            )
        segmentos = session.scalars(
            sa.select(NlpProcurementSegment).where(
                sa.tuple_(NlpProcurementSegment.source, NlpProcurementSegment.external_id).in_(
                    oportunidades
                ),
                NlpProcurementSegment.source == "PNCP",
                NlpProcurementSegment.status == "READY",
                NlpProcurementSegment.normalized_text.is_not(None),
            )
        ).all()

    query_rows = []
    sem_descricao = 0
    for company in companies:
        cnaes = company.get("cnaes") or []
        partes: list[dict[str, Any]] = []
        for cnae in cnaes:
            descricao = (cnae.get("description") or "").strip()
            if descricao:
                partes.append({
                    "code": str(cnae.get("code", "")),
                    "is_primary": bool(cnae.get("is_primary")),
                    "text": f"CNAE {'principal' if cnae.get('is_primary') else 'secundário'}: {descricao}",
                })
            else:
                sem_descricao += 1
                partes.append({
                    "code": str(cnae.get("code", "")),
                    "is_primary": bool(cnae.get("is_primary")),
                    "text": f"CNAE {'principal' if cnae.get('is_primary') else 'secundário'} código {cnae.get('code', '')}",
                })
        if not partes:
            partes = [{
                "code": str(company.get("primary_cnae", "")),
                "is_primary": True,
                "text": f"CNAE principal código {company.get('primary_cnae', '')}",
            }]
        query_rows.append({
            "query_id": str(company["company_id"]),
            "profile_text": "; ".join(parte["text"] for parte in partes),
            "profile_parts": partes,
            "judgments": {},
        })

    chaves = {f"{source}|{external_id}" for source, external_id in oportunidades}
    for query in query_rows:
        query["judgments"] = {key: None for key in sorted(chaves)}
    documents = [
        {
            "procurement_id": f"{segment.source}|{segment.external_id}",
            "segment_type": segment.segment_type,
            "text": segment.normalized_text,
        }
        for segment in segmentos
    ]
    dataset_fingerprint = sha256(json.dumps({
        "companies": [(query["query_id"], query["profile_parts"]) for query in query_rows],
        "opportunities": sorted(chaves),
        "segments": [(doc["procurement_id"], doc["segment_type"], doc["text"]) for doc in documents],
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return {
        "dataset_version": f"pncp-amostra-v1-{dataset_fingerprint}",
        "label_instructions": "Preencha cada judgment com 0=irrelevante, 1=incerto ou 2=relevante.",
        "source": "nlp_procurement_segments (PNCP normalizado)",
        "selected_opportunities": len(chaves),
        "company_profiles": len(query_rows),
        "cnae_entries_without_description": sem_descricao,
        "queries": query_rows,
        "documents": documents,
    }


def _rss_mb() -> float | None:
    try:
        import psutil
        import os
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return None


class _RSSSampler:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self.initial_mb: float | None = None
        self.last_mb: float | None = None
        self.peak_mb: float | None = None
        self._process: Any = None
        self._stop = Event()
        self._thread: Thread | None = None

    def _sample(self) -> None:
        if self._process is None:
            return
        try:
            value = self._process.memory_info().rss / (1024 * 1024)
        except Exception:
            return
        self.last_mb = value
        self.peak_mb = value if self.peak_mb is None else max(self.peak_mb, value)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self) -> _RSSSampler:
        try:
            import psutil
        except ImportError:
            return self
        self._process = psutil.Process()
        self._sample()
        self.initial_mb = self.last_mb
        self._thread = Thread(target=self._run, name="nlp-rss-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()


def avaliar(dataset: dict[str, Any], *, batch_size: int) -> dict[str, Any]:
    _validar(dataset)
    docs = dataset["documents"]
    queries = dataset["queries"]
    textos_docs = [item["text"] for item in docs]
    textos_queries = [item["profile_text"] for item in queries]
    textos_partes, grupos_partes, grupos_principais = _grupos_de_partes(queries)
    ks = [5, 10]

    # Ajusta IDF lexical em todo o vocabulário e normaliza cada vetor.
    lexical_all = _tfidf(textos_queries + textos_docs)
    lexical_queries = lexical_all[:len(textos_queries)]
    lexical_docs = lexical_all[len(textos_queries):]
    lexical_results: dict[str, Any] = {}
    for query, vector in zip(queries, lexical_queries):
        scores = [_cosine(vector, passage) for passage in lexical_docs]
        ranking = _rank_segmentos(scores, docs)
        lexical_results[query["query_id"]] = _metricas(ranking, query["judgments"], ks)

    lexical_part_all = _tfidf(textos_partes + textos_docs)
    lexical_part_queries = lexical_part_all[:len(textos_partes)]
    lexical_part_docs = lexical_part_all[len(textos_partes):]
    lexical_part_scores = np.asarray([
        [_cosine(vector, passage) for passage in lexical_part_docs]
        for vector in lexical_part_queries
    ], dtype=np.float32)
    lexical_max = _max_scores_por_grupo(lexical_part_scores, grupos_partes)
    lexical_primary = _max_scores_por_grupo(lexical_part_scores, grupos_principais)
    lexical_part_results: dict[str, Any] = {}
    lexical_primary_results: dict[str, Any] = {}
    for query, all_scores, primary_scores in zip(queries, lexical_max, lexical_primary):
        lexical_part_results[query["query_id"]] = _metricas(
            _rank_segmentos(all_scores, docs), query["judgments"], ks
        )
        lexical_primary_results[query["query_id"]] = _metricas(
            _rank_segmentos(primary_scores, docs), query["judgments"], ks
        )

    model = E5EmbeddingModel(batch_size=batch_size)
    rss_before = _rss_mb()
    rss_sampler = _RSSSampler()
    load_start, load_cpu = perf_counter(), process_time()
    with rss_sampler:
        model.load()
        model_load_seconds = perf_counter() - load_start
        model_load_cpu_seconds = process_time() - load_cpu
        rss_after_load = _rss_mb()
        query_start, query_cpu = perf_counter(), process_time()
        query_batch = model.encode(textos_queries, input_type="query")
        query_seconds = perf_counter() - query_start
        query_cpu_seconds = process_time() - query_cpu
        rss_after_queries = _rss_mb()
        cnae_start, cnae_cpu = perf_counter(), process_time()
        cnae_batch = model.encode(textos_partes, input_type="query")
        cnae_seconds = perf_counter() - cnae_start
        cnae_cpu_seconds = process_time() - cnae_cpu
        rss_after_cnaes = _rss_mb()
        docs_start, docs_cpu = perf_counter(), process_time()
        passage_batch = model.encode(textos_docs, input_type="passage")
        passage_seconds = perf_counter() - docs_start
        passage_cpu_seconds = process_time() - docs_cpu
        rss_after_inference = _rss_mb()
    semantic_results: dict[str, Any] = {}
    matrix_docs = np.asarray(passage_batch.vectors, dtype=np.float32)
    matrix_cnaes = np.asarray(cnae_batch.vectors, dtype=np.float32)
    cnae_scores = matrix_cnaes @ matrix_docs.T
    e5_all_cnae_scores = _max_scores_por_grupo(cnae_scores, grupos_partes)
    e5_primary_scores = _max_scores_por_grupo(cnae_scores, grupos_principais)
    e5_part_results: dict[str, Any] = {}
    e5_primary_results: dict[str, Any] = {}
    for query, all_scores, primary_scores in zip(queries, e5_all_cnae_scores, e5_primary_scores):
        e5_part_results[query["query_id"]] = _metricas(
            _rank_segmentos(all_scores, docs), query["judgments"], ks
        )
        e5_primary_results[query["query_id"]] = _metricas(
            _rank_segmentos(primary_scores, docs), query["judgments"], ks
        )
    for query, vector in zip(queries, query_batch.vectors):
        scores = (matrix_docs @ np.asarray(vector, dtype=np.float32)).tolist()
        ranking = _rank_segmentos(scores, docs)
        semantic_results[query["query_id"]] = _metricas(ranking, query["judgments"], ks)

    return {
        "dataset_version": dataset["dataset_version"],
        "model_version": query_batch.model_version,
        "counts": {"queries": len(queries), "cnae_query_parts": len(textos_partes),
                   "segments": len(docs),
                   "opportunities": len({item["procurement_id"] for item in docs})},
        "latency": {"model_load_seconds": model_load_seconds,
                    "model_load_cpu_seconds": model_load_cpu_seconds,
                    "query_encode_seconds": query_seconds,
                    "query_encode_cpu_seconds": query_cpu_seconds,
                    "cnae_query_encode_seconds": cnae_seconds,
                    "cnae_query_encode_cpu_seconds": cnae_cpu_seconds,
                    "passage_encode_seconds": passage_seconds,
                    "passage_encode_cpu_seconds": passage_cpu_seconds,
                    "passage_cpu_percent_of_one_core": 100 * passage_cpu_seconds / passage_seconds if passage_seconds else None,
                    "passages_per_second": len(docs) / passage_seconds if passage_seconds else None},
        "rss_mb": {"before_model_load": rss_before,
                   "after_model_load": rss_after_load,
                   "after_full_profile_queries": rss_after_queries,
                   "after_cnae_queries": rss_after_cnaes,
                   "after_passage_inference": rss_after_inference,
                   "peak_during_model_and_inference": rss_sampler.peak_mb,
                   "sampling_interval_seconds": rss_sampler.interval_seconds,
                   "measurement_available": rss_before is not None and rss_sampler.peak_mb is not None},
        "results": {"e5": semantic_results, "e5_per_cnae_max": e5_part_results,
                    "e5_primary_only": e5_primary_results,
                    "tfidf_word_char": lexical_results,
                    "tfidf_per_cnae_max": lexical_part_results,
                    "tfidf_primary_only": lexical_primary_results},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", help="JSON PNCP rotulado")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=Path, help="Arquivo JSON de saída; padrão stdout")
    parser.add_argument("--prepare-from-db", action="store_true", help="Exporta amostra de segmentos armazenados")
    parser.add_argument(
        "--companies", type=Path,
        help="Arquivo JSON explícito de perfis para preparar a amostra do benchmark.",
    )
    parser.add_argument("--limit", type=int, default=25, help="Quantidade máxima de editais amostrados")
    parser.add_argument("--companies-limit", type=int, default=10, help="Perfis iniciais de companies.json")
    parser.add_argument("--sample-output", type=Path, help="Arquivo de amostra a rotular")
    args = parser.parse_args()
    if args.prepare_from_db:
        if not args.sample_output:
            parser.error("--prepare-from-db requer --sample-output")
        if not args.companies:
            parser.error("--prepare-from-db requer --companies explícito; não há fixture padrão")
        try:
            sample = _preparar_amostra_banco(args.companies, args.limit, args.companies_limit)
            args.sample_output.parent.mkdir(parents=True, exist_ok=True)
            args.sample_output.write_text(
                json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError, RuntimeError) as exc:
            print(f"Amostra não preparada: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "sample_output": str(args.sample_output),
            "companies": sample["company_profiles"],
            "opportunities": sample["selected_opportunities"],
            "company_profiles": sample["company_profiles"],
            "segments": len(sample["documents"]),
            "cnae_entries_without_description": sample["cnae_entries_without_description"],
            "next_step": "preencha todos os judgments com 0, 1 ou 2 e rode novamente sem --prepare-from-db",
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.dataset:
        parser.error("informe o dataset rotulado ou use --prepare-from-db")
    try:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        report = avaliar(dataset, batch_size=args.batch_size)
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(f"Benchmark não executado: {exc}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
