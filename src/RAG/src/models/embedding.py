"""Sentence embedding helpers aligned with the notebook configuration."""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, List

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import EMBED_BATCH_SIZE, EMBED_DEVICE, EMBED_MODEL_NAME


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """Return a cached SentenceTransformer instance."""
    model = SentenceTransformer(
        EMBED_MODEL_NAME,
        trust_remote_code=True,
        device=EMBED_DEVICE,
    )
    return model


def encode_texts(texts: Iterable[str], normalize: bool = True) -> np.ndarray:
    """Encode a list of texts into dense vectors using the project defaults."""
    embedder = get_embedder()
    vectors = embedder.encode(
        list(texts),
        batch_size=EMBED_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    return vectors


__all__ = ["get_embedder", "encode_texts"]
