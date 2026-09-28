"""Artifact-free retrieval parity and synthetic latency benchmark.

Run ``python tests/test_bounded_concurrent_retrieval.py`` to print p50/p95 for
mocked sequential and concurrent dataset searches.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import numpy as np
import pytest
from anyio.to_thread import current_default_thread_limiter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import rag_service as service  # noqa: E402
from scripts.report_stage_latency import percentile  # noqa: E402
from src.models import embedding  # noqa: E402
from src.vectorstore import chroma_client  # noqa: E402


def _mock_search_setup(delay: float = 0.02, *, unavailable: bool = True):
    frame = pd.DataFrame([{
        "chunk_id": "official:1", "document_key": "official:1",
        "title": "Official", "chunk_text": "Official evidence",
        "url": "https://www.dongguk.edu/official", "hybrid_score": 0.75,
    }])
    active = 0
    peak = 0
    guard = threading.Lock()

    def ensure(dataset):
        if dataset == "staff" and unavailable:
            raise ValueError("unavailable fixture")
        return frame, None, None, None

    def search(**kwargs):
        nonlocal active, peak
        dataset = next(
            key for key, artifacts in service.DATASET_ARTIFACTS.items()
            if artifacts.collection == kwargs["collection_name"]
        )
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            # The second route entry finishes after the first, exposing any
            # completion-order merge in the response or trace.
            time.sleep(delay * (2 if dataset == "courses" else 1))
            hit = frame.copy()
            hit["chunk_id"] = f"{dataset}:{kwargs['query']}"
            hit["document_key"] = f"{dataset}:official"
            hit["hybrid_score"] = 0.75 if dataset == "rules" else 0.42
            hit.attrs.update(
                retrieval_mode="sparse_degraded" if dataset == "courses" else "hybrid",
                dense_error_type="InternalError" if dataset == "courses" else None,
            )
            return hit
        finally:
            with guard:
                active -= 1

    return ensure, search, lambda: peak


def _retrieve():
    token = service._retrieval_observations.set([])
    try:
        frames, eliminated, unavailable = asyncio.run(service._retrieve_frames_for_queries(
            route=["rules", "staff", "courses"], queries=["original", "expanded-1", "expanded-2"],
            final_where_filter={}, notice_board_filter=None, date_filter=None,
            entry_year=None, request_id="synthetic",
        ))
        return (
            [frame.to_dict(orient="records") for frame in frames], eliminated,
            unavailable, list(service._retrieval_observations.get()), service._retrieval_summary(),
        )
    finally:
        service._retrieval_observations.reset(token)


def test_concurrency_preserves_query_route_order_scores_and_degraded_trace(monkeypatch):
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_: True)
    outcomes = []
    for concurrency in (1, 2, 3):
        ensure, search, peak = _mock_search_setup()
        monkeypatch.setattr(service, "_ensure_dataset", ensure)
        monkeypatch.setattr(service, "hybrid_search_with_meta", search)
        monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", concurrency)
        outcome = _retrieve()
        outcomes.append(outcome)
        assert 1 <= peak() <= concurrency
        if concurrency > 1:
            assert peak() >= 2

    assert outcomes[0] == outcomes[1] == outcomes[2]
    frames, eliminated, unavailable, observations, summary = outcomes[0]
    assert not eliminated
    assert unavailable == ["staff"]
    assert [(frame[0]["matched_query"], frame[0]["dataset"], frame[0]["hybrid_score"])
            for frame in frames] == [
                (query, dataset, score)
                for query in ("original", "expanded-1", "expanded-2")
                for dataset, score in (("rules", 0.75), ("courses", 0.42))
            ]
    assert [item["dataset"] for item in observations] == ["rules", "courses"] * 3
    assert summary == {"retrieval_mode": "sparse_degraded", "degraded_datasets": ["courses"]}


def test_unexpected_dataset_search_error_still_propagates(monkeypatch):
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_: True)
    frame = pd.DataFrame([{"chunk_id": "rules:1"}])
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (frame, None, None, None))
    monkeypatch.setattr(
        service, "hybrid_search_with_meta",
        lambda **_: (_ for _ in ()).throw(RuntimeError("unexpected search failure")),
    )
    monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", 2)
    with pytest.raises(RuntimeError, match="unexpected search failure"):
        asyncio.run(service._retrieve_frames(
            route=["rules", "courses"], query="query", final_where_filter={},
            notice_board_filter=None, date_filter=None, entry_year=None,
            request_id="unexpected-error",
        ))


def test_unexpected_dataset_errors_raise_first_route_failure_not_fastest(monkeypatch):
    frame = pd.DataFrame([{"chunk_id": "official:1"}])
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (frame, None, None, None))
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_: True)

    def failing_search(**kwargs):
        collection = kwargs["collection_name"]
        if collection == service.DATASET_ARTIFACTS["rules"].collection:
            time.sleep(0.04)
            raise RuntimeError("rules failed first in route")
        raise RuntimeError("courses failed faster")

    monkeypatch.setattr(service, "hybrid_search_with_meta", failing_search)
    for concurrency in (1, 2):
        monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", concurrency)
        token = service._retrieval_observations.set([])
        try:
            with pytest.raises(RuntimeError, match="rules failed first in route"):
                asyncio.run(service._retrieve_frames(
                    route=["rules", "courses"], query="query", final_where_filter={},
                    notice_board_filter=None, date_filter=None, entry_year=None,
                    request_id="ordered-dataset-errors",
                ))
            assert service._retrieval_observations.get() == []
        finally:
            service._retrieval_observations.reset(token)


def test_unexpected_expansion_errors_raise_first_query_failure(monkeypatch):
    frame = pd.DataFrame([{
        "chunk_id": "official:1", "document_key": "official:1",
        "chunk_text": "Official evidence", "hybrid_score": 0.75,
    }])
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (frame, None, None, None))
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_: True)

    def search(**kwargs):
        query = kwargs["query"]
        if query == "expanded-slow":
            time.sleep(0.04)
            raise RuntimeError("first expansion failed")
        if query == "expanded-fast":
            raise RuntimeError("later expansion failed faster")
        return frame.copy()

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)
    monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", 3)
    token = service._retrieval_observations.set([])
    try:
        with pytest.raises(RuntimeError, match="first expansion failed"):
            asyncio.run(service._retrieve_frames_for_queries(
                route=["rules"], queries=["original", "expanded-slow", "expanded-fast"],
                final_where_filter={}, notice_board_filter=None, date_filter=None,
                entry_year=None, request_id="ordered-query-errors",
            ))
        assert service._retrieval_observations.get() == []
    finally:
        service._retrieval_observations.reset(token)


def test_cold_query_embedding_is_encoded_once_before_parallel_searches(monkeypatch):
    class CountingEmbedder:
        calls = 0

        def encode(self, texts, **_kwargs):
            self.calls += 1
            time.sleep(0.01)
            return np.asarray([[float(len(texts[0])), 1.0]], dtype=np.float32)

    frame = pd.DataFrame([{
        "chunk_id": "official:1", "document_key": "official:1",
        "chunk_text": "Official evidence", "hybrid_score": 0.75,
    }])
    fake = CountingEmbedder()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    monkeypatch.setattr(service, "get_collection", lambda _: object())
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (frame, None, None, None))
    monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", 3)

    def search(**kwargs):
        # The production hybrid search reads the same cached encoder after
        # the service primes it. Keep that call in this artifact-free fixture.
        embedding.encode_queries([kwargs["query"]])
        return frame.copy()

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)
    embedding.clear_query_embedding_cache()
    try:
        asyncio.run(service._retrieve_frames_for_queries(
            route=["rules", "courses", "staff"],
            queries=["cold-original", "cold-expanded", "cold-expanded"],
            final_where_filter={}, notice_board_filter=None, date_filter=None,
            entry_year=None, request_id="single-encode",
        ))
        assert fake.calls == 2
    finally:
        embedding.clear_query_embedding_cache()


def test_ensure_dataset_loads_once_under_concurrent_first_access(monkeypatch, tmp_path):
    dataset = "rules"
    artifacts = SimpleNamespace(chunk_path=tmp_path / "missing.parquet", collection="unused")
    monkeypatch.setitem(service.DATASET_ARTIFACTS, dataset, artifacts)
    monkeypatch.setattr(service, "live_lexical_index_path", lambda _key: tmp_path / "missing.fts")
    calls = []

    def loader():
        calls.append(1)
        time.sleep(0.02)
        return pd.DataFrame([{
            "chunk_id": "rules:1", "document_key": "rules:1",
            "title": "Official", "chunk_text": "Official evidence",
        }]), None, None

    monkeypatch.setitem(service._DATASET_LOADERS, dataset, loader)
    with service._datasets_lock:
        previous = service._datasets.pop(dataset, None)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(service._ensure_dataset, [dataset] * 12))
        assert len(calls) == 1
        assert all(result[0] is results[0][0] for result in results)
    finally:
        with service._datasets_lock:
            service._datasets.pop(dataset, None)
            if previous is not None:
                service._datasets[dataset] = previous


def test_same_dataset_search_lock_guards_collection_handle():
    active = 0
    peak = 0
    guard = threading.Lock()

    def search():
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with guard:
            active -= 1

    async def run_searches():
        await asyncio.gather(*(service._search_dataset("rules", search) for _ in range(6)))

    asyncio.run(run_searches())
    assert peak == 1


def test_many_requests_finish_with_small_threadpool(monkeypatch):
    """Waiting for a collection gate never occupies a worker slot."""
    frame = pd.DataFrame([{"chunk_id": "official:1", "chunk_text": "evidence"}])
    monkeypatch.setattr(service, "_ensure_dataset", lambda _: (frame, None, None, None))
    monkeypatch.setattr(service, "_prime_query_embedding", lambda *_: True)
    monkeypatch.setattr(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", 3)

    def search(**kwargs):
        time.sleep(0.005)
        hit = frame.copy()
        hit["chunk_id"] = f"{kwargs['collection_name']}:{kwargs['query']}"
        return hit

    monkeypatch.setattr(service, "hybrid_search_with_meta", search)

    async def run_requests():
        limiter = current_default_thread_limiter()
        original_tokens = limiter.total_tokens
        limiter.total_tokens = 2
        try:
            await asyncio.wait_for(asyncio.gather(*(
                service._retrieve_frames_for_queries(
                    route=["rules", "courses", "staff"],
                    queries=[f"original-{index}", f"expanded-{index}"],
                    final_where_filter={}, notice_board_filter=None, date_filter=None,
                    entry_year=None, request_id=f"request-{index}",
                )
                for index in range(12)
            )), timeout=5)
        finally:
            limiter.total_tokens = original_tokens

    asyncio.run(run_requests())


def test_local_chroma_client_queries_two_collections_from_threads(monkeypatch, tmp_path):
    """Exercise the installed Chroma read path with disposable collections."""
    monkeypatch.setattr(chroma_client, "CHROMA_DIR", tmp_path)
    monkeypatch.setattr(chroma_client, "_client_instance", None)
    chroma_client.get_collection.cache_clear()
    try:
        client = chroma_client.get_client()
        for name in ("rules-synthetic", "courses-synthetic"):
            collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
            collection.add(ids=[f"{name}:1"], embeddings=[[1.0, 0.0]], documents=["official"])

        def query(name):
            result = chroma_client.query_items(
                name, query_embeddings=[[1.0, 0.0]], n_results=1,
            )
            return result["ids"][0]

        names = ["rules-synthetic", "courses-synthetic"] * 6
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(query, names))
        assert results == [[f"{name}:1"] for name in names]
    finally:
        chroma_client.get_collection.cache_clear()


def run_synthetic_benchmark(rounds: int = 15, delay: float = 0.03):
    results = {}
    for concurrency in (1, 2, 3):
        ensure, search, _ = _mock_search_setup(delay, unavailable=False)
        with patch.object(service, "_ensure_dataset", ensure), \
             patch.object(service, "hybrid_search_with_meta", search), \
             patch.object(service, "_prime_query_embedding", return_value=True), \
             patch.object(service.rag_config, "RAG_RETRIEVAL_CONCURRENCY", concurrency):
            samples = []
            for _ in range(rounds):
                started = time.perf_counter()
                asyncio.run(service._retrieve_frames(
                    route=["rules", "courses", "staff"], query="synthetic",
                    final_where_filter={}, notice_board_filter=None, date_filter=None,
                    entry_year=None, request_id="synthetic-benchmark",
                ))
                samples.append((time.perf_counter() - started) * 1000)
        results[str(concurrency)] = {
            "p50_ms": percentile(samples, 0.5), "p95_ms": percentile(samples, 0.95),
        }
    return results


def test_synthetic_benchmark_reports_percentiles_without_real_artifacts():
    report = run_synthetic_benchmark(rounds=3, delay=0.01)
    assert set(report) == {"1", "2", "3"}
    assert all(row["p50_ms"] > 0 and row["p95_ms"] > 0 for row in report.values())


if __name__ == "__main__":
    print(json.dumps(run_synthetic_benchmark(), indent=2))
