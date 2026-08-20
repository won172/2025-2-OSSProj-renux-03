from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import embedding  # noqa: E402


def test_query_embedding_is_reused_across_routerless_dataset_searches(monkeypatch):
    class FakeEmbedder:
        calls = 0

        def encode(self, texts, **_kwargs):
            self.calls += 1
            return np.asarray([[float(len(texts[0])), 1.0]], dtype=np.float32)

    fake = FakeEmbedder()
    monkeypatch.setattr(embedding, "get_embedder", lambda: fake)
    embedding.clear_query_embedding_cache()
    try:
        first = embedding.encode_queries(["같은 질문"])
        second = embedding.encode_queries(["같은 질문"])
    finally:
        embedding.clear_query_embedding_cache()

    assert fake.calls == 1
    assert np.array_equal(first, second)
    assert first is not second


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        (np.asarray([[1.0, 2.0]]), "row count mismatch"),
        (np.empty((2, 0), dtype=np.float32), "dimension must be positive"),
        (np.asarray([[1.0, np.nan], [2.0, 3.0]]), "non-finite"),
    ],
)
def test_passage_embedding_validation_rejects_invalid_model_output(monkeypatch, vectors, message):
    class FakeEmbedder:
        def encode(self, _texts, **_kwargs):
            return vectors

    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder())

    with pytest.raises(ValueError, match=message):
        embedding.encode_texts(["첫 문서", "둘째 문서"])


def test_passage_embedding_validation_preserves_valid_matrix(monkeypatch):
    expected = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)

    class FakeEmbedder:
        def encode(self, _texts, **_kwargs):
            return expected

    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder())

    actual = embedding.encode_texts(["첫 문서", "둘째 문서"])

    assert np.array_equal(actual, expected)
