"""Answer generation helpers that wrap the OpenAI chat API."""
from __future__ import annotations

from functools import lru_cache
from typing import Tuple

import pandas as pd
from openai import OpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

ANSWER_PROMPT_TEMPLATE = """당신은 동국대학교 캠퍼스 RAG 어시스턴트입니다. \
반드시 아래 텍스트만 근거로 한국어로 답변하세요. \
최근 날짜의 공지사항만 답변에 포함하세요. \
모호하면 "제공된 자료에서 확인되지 않습니다"라고 답하세요. \
날짜/시간은 YYYY-MM-DD 또는 HH:MM 형식으로 정규화해 주세요.\n\n질문: {question}\n\n[관련 공지]\n{context}\n\n[출력 형식]\n- 📌 핵심 요약: (한 줄)\n- 📅 일정/마감:\n- 📋 조건/대상/방법(있다면):\n- 📎 참고 링크: (최대 3개)\n- ⚠️ 주의: (자료에서 명확하지 않은 점이 있으면)\n\n답변만 작성하세요."""


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)


def extract_title(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    if text.startswith("[") and "]" in text:
        closing = text.find("]")
        if closing > 1:
            return text[1:closing].strip()
    return text.split("\n", 1)[0].strip()[:120]


def format_citations(df: pd.DataFrame) -> str:
    lines = []
    for _, row in df.iterrows():
        title = extract_title(row.get("chunk_text", ""))
        date = row.get("published_at")
        url = row.get("url")
        if url and date:
            lines.append(f"- {title} ({date}) — {url}")
        elif url:
            lines.append(f"- {title} — {url}")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)


def build_context(df: pd.DataFrame) -> str:
    return "\n\n---\n\n".join(df["chunk_text"].tolist())


def answer_with_citations(
    query: str,
    hits: pd.DataFrame,
    model_name: str = OPENAI_MODEL,
    temperature: float = 0.2,
) -> Tuple[str, str]:
    if hits.empty:
        return "제공된 자료에서 확인되지 않습니다.", ""

    context = build_context(hits)
    citations = format_citations(hits)
    prompt = ANSWER_PROMPT_TEMPLATE.format(question=query, context=context)

    client = get_client()
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    answer = response.choices[0].message.content.strip()
    return answer, citations


__all__ = ["answer_with_citations", "format_citations", "extract_title"]
