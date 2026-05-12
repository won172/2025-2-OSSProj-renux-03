from __future__ import annotations

import logging
from typing import Dict, List, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from src.config import OPENAI_MODEL, QUERY_ANALYSIS_MAX_QUERIES

VALID_INTENTS = {"notices", "rules", "schedule", "staff", "courses", "smalltalk", "unknown"}
VALID_TIME_FOCUS = {"today", "recent", "this_week", "this_month", "none"}


class QueryAnalysisResult(BaseModel):
    normalized_question: str = Field(description="원문 의미를 크게 바꾸지 않은 정규화 질문")
    intent: Literal["notices", "rules", "schedule", "staff", "courses", "smalltalk", "unknown"]
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    time_focus: Literal["today", "recent", "this_week", "this_month", "none"] = "none"
    search_queries: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: str | None = None


parser = PydanticOutputParser(pydantic_object=QueryAnalysisResult)

prompt = PromptTemplate(
    template="""당신은 동국대학교 RAG 검색을 위한 질의분석기입니다.
사용자 질문을 검색 친화적으로 구조화하세요.

규칙:
1. 답변을 만들지 말고 검색을 위한 분석만 하세요.
2. 원문 의미를 바꾸지 마세요.
3. 추측으로 특정 장학금/학과/부서를 단정하지 마세요.
4. search_queries는 최대 {max_queries}개만 생성하세요.
5. search_queries에는 원문을 크게 벗어나지 않는 검색용 표현만 넣으세요.
6. intent는 반드시 다음 중 하나만 고르세요:
   notices, rules, schedule, staff, courses, smalltalk, unknown
7. time_focus는 반드시 다음 중 하나만 고르세요:
   today, recent, this_week, this_month, none
8. 모호하지만 검색은 가능하면 needs_clarification=true로 두고도 search_queries는 생성하세요.

intent 기준:
- notices: 장학, 모집, 발표, 일반 공지, 최신 공지
- rules: 학칙, 규정, 휴학, 복학, 재수강, 수강취소, 졸업, 성적 규정
- schedule: 개강, 종강, 시험, 수강신청 기간, 학사일정
- staff: 전화번호, 내선, 부서 연락처, 사무실
- courses: 교과과정, 전공필수, 선수과목, 이수구분, 개설 과목
- smalltalk: 인사, 감사, 가벼운 잡담

사용자 질문:
{query}

JSON 형식:
{format_instructions}
""",
    input_variables=["query"],
    partial_variables={
        "format_instructions": parser.get_format_instructions(),
        "max_queries": str(QUERY_ANALYSIS_MAX_QUERIES),
    },
)

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0,
    timeout=20,
    model_kwargs={"response_format": {"type": "json_object"}},
)
analysis_chain = prompt | llm | parser


async def analyze_query(query: str) -> QueryAnalysisResult | None:
    if not query.strip():
        return None

    try:
        result = await analysis_chain.ainvoke({"query": query})
    except ValidationError as exc:
        logging.warning("Query analysis validation failed: %s", exc)
        return None
    except Exception as exc:
        logging.warning("Query analysis failed: %s", exc)
        return None

    search_queries = []
    for candidate in result.search_queries:
        cleaned = candidate.strip()
        if cleaned and cleaned not in search_queries:
            search_queries.append(cleaned)
        if len(search_queries) >= QUERY_ANALYSIS_MAX_QUERIES:
            break

    intent = result.intent if result.intent in VALID_INTENTS else "unknown"
    time_focus = result.time_focus if result.time_focus in VALID_TIME_FOCUS else "none"

    return QueryAnalysisResult(
        normalized_question=result.normalized_question.strip() or query.strip(),
        intent=intent,
        entities=result.entities or {},
        time_focus=time_focus,
        search_queries=search_queries,
        needs_clarification=result.needs_clarification,
        clarification_reason=result.clarification_reason,
    )


__all__ = ["QueryAnalysisResult", "analyze_query"]
