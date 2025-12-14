"""OpenAI 채팅 API를 감싸 답변을 생성하는 헬퍼입니다."""
from __future__ import annotations

from functools import lru_cache
from typing import Tuple

import pandas as pd
from openai import OpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

ANSWER_PROMPT_TEMPLATE = """
당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 
오늘 날짜: {current_date}\n\n[지침]\n
1. [컨텍스트] 내용만으로 답변하세요. 없는 정보는 지어내지 마세요.\n
2. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [컨텍스트]에 있다면 해당 설명 바로 아래에 \"URL: (링크주소)\" 형식으로 적어주세요. 절대 마크다운 링크([텍스트](URL))로 변환하지 말고 주소만 그대로 쓰세요. 주소가 없다면 URL에 대해 쓰지 마세요.\n
3. 친절한 한국어(해요체)로 답변하세요.\n
4. 절차나 방법은 번호를 매겨 단계별로 설명하세요.\n
5. 정보가 없으면 정중히 사과하고 재검색을 유도하세요.\n
6. {current_date} 기준 최신 정보를 우선하세요.\n
7. 답변에 볼드체(**) 등 마크다운 서식을 절대 사용하지 마세요.\n
8. 이전 대화 맥락을 고려하되, 현재 질문이 주제가 바뀌었다면 이전 내용은 무시하고 현재 질문에 집중하세요.\n
9. 질문에 '최근', '어제' 등 시간 표현이 있다면, 제공된 [컨텍스트] 내 문서의 '게시일'과 현재 날짜({current_date})를 비교하여 정확히 계산하고 답변하세요.\n\n
[컨텍스트]\n
{context}
""",


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
