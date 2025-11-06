"""Chat-oriented wrapper that keeps short-term history."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import pandas as pd

from src.config import DEFAULT_TOP_K, HYBRID_ALPHA
from src.search.hybrid import hybrid_search_with_meta
from src.services.answer import answer_with_citations


def _last_user_question(history: List[dict]) -> str | None:
    for message in reversed(history):
        if message.get("role") == "user":
            return message.get("content")
    return None


@dataclass
class ChatSession:
    collection_name: str
    chunks_df: pd.DataFrame
    tfidf_vectorizer
    tfidf_matrix
    history: List[dict] = field(default_factory=list)

    def ask(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        alpha: float = HYBRID_ALPHA,
    ) -> Tuple[str, str]:
        query = query.strip()
        if not query:
            return "질문이 비어 있습니다. 구체적으로 입력해 주세요.", ""

        self.history.append({"role": "user", "content": query})

        hits = hybrid_search_with_meta(
            self.collection_name,
            self.chunks_df,
            self.tfidf_vectorizer,
            self.tfidf_matrix,
            query,
            top_k=top_k,
            alpha=alpha,
        )
        answer, citations = answer_with_citations(query, hits)
        combined = f"{answer}\n\n🔗 출처:\n{citations}".strip()
        self.history.append({"role": "assistant", "content": combined})
        return combined, citations


__all__ = ["ChatSession"]
