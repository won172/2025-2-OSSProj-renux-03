from __future__ import annotations

import logging
from typing import Dict, List, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

from src.config import OPENAI_MODEL, QUERY_ANALYSIS_MAX_QUERIES, RAG_MAX_SUBQUERIES

VALID_INTENTS = {"notices", "rules", "schedule", "staff", "courses", "unknown"}
VALID_DATASETS = {"notices", "rules", "schedule", "staff", "courses"}
VALID_TIME_FOCUS = {"today", "recent", "this_week", "this_month", "none"}


class SubQuery(BaseModel):
    """복합 질문을 측면별로 분해한 단위 검색. dataset은 이 서브쿼리가 노릴 데이터셋."""
    query: str = Field(description="해당 측면을 검색하기 위한 독립적인 검색 문장")
    dataset: Literal["notices", "rules", "schedule", "staff", "courses"]


class QueryAnalysisResult(BaseModel):
    normalized_question: str = Field(description="원문 의미를 크게 바꾸지 않은 정규화 질문")
    intent: Literal["notices", "rules", "schedule", "staff", "courses", "unknown"]
    entities: Dict[str, List[str]] = Field(default_factory=dict)
    time_focus: Literal["today", "recent", "this_week", "this_month", "none"] = "none"
    search_queries: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: str | None = None
    # 복합 질문(졸업/수강 계획 등)일 때만 채워진다. 단순 질문은 빈 값.
    is_compound: bool = False
    sub_queries: List[SubQuery] = Field(default_factory=list)

    @property
    def decomposed_datasets(self) -> List[str]:
        """sub_queries가 가리키는 데이터셋의 합집합(출현 순서 보존)."""
        seen: List[str] = []
        for sq in self.sub_queries:
            if sq.dataset in VALID_DATASETS and sq.dataset not in seen:
                seen.append(sq.dataset)
        return seen


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
   notices, rules, schedule, staff, courses, unknown
7. time_focus는 반드시 다음 중 하나만 고르세요:
   today, recent, this_week, this_month, none
8. 모호하지만 검색은 가능하면 needs_clarification=true로 두고도 search_queries는 생성하세요.
9. [이전 대화]가 있고 현재 질문이 후속 질문(대명사·생략 포함, 예: "그럼 신청 기간은?")이면,
   이전 대화의 주제를 반영해 normalized_question과 search_queries를 **독립적으로 이해 가능한
   완전한 질문**으로 재작성하세요. 단, 현재 질문이 새로운 주제면 이전 대화는 무시하세요.
10. 질문이 **여러 종류의 정보를 동시에 요구하는 복합 질문**이면(예: "졸업하려면 뭘 해야 해?",
    "수강 계획 세워줘", "복수전공 졸업 준비") is_compound=true로 두고 sub_queries를 만드세요.
    - sub_queries는 질문을 측면별로 쪼갠 독립 검색이며, 각 항목에 가장 알맞은 dataset 하나를 지정합니다.
    - 예: 졸업 준비 → [요건은 rules, 전공/이수과목은 courses, 수강신청·졸업논문 일정은 schedule,
      학과 상담 연락처는 staff, 관련 공지/장학은 notices]. 질문 맥락에 실제로 필요한 측면만 넣으세요.
    - sub_queries에는 학과/전공 등 질문에 드러난 구체 정보를 반영하세요(예: "통계학과 전공필수 과목").
    - 최대 {max_subqueries}개. 단순 질문(단일 측면)이면 is_compound=false, sub_queries=[]로 두세요.

[이전 대화]
{history}

intent 기준(가장 중심이 되는 단일 측면):
- notices: 장학, 모집, 발표, 일반 공지, 최신 공지
- rules: 학칙, 규정, 휴학, 복학, 재수강, 수강취소, 졸업, 성적 규정
- schedule: 개강, 종강, 시험, 수강신청 기간, 학사일정
- staff: 전화번호, 내선, 부서 연락처, 사무실
- courses: 교과과정, 전공필수, 선수과목, 이수구분, 개설 과목
사용자 질문:
{query}

JSON 형식:
{format_instructions}
""",
    input_variables=["query", "history"],
    partial_variables={
        "format_instructions": parser.get_format_instructions(),
        "max_queries": str(QUERY_ANALYSIS_MAX_QUERIES),
        "max_subqueries": str(RAG_MAX_SUBQUERIES),
    },
)

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0,
    timeout=20,
    max_retries=1,  # 질의분석은 실패해도 raw 질문으로 폴백되므로 재시도 1회로 TTFB 누적 방지
    model_kwargs={"response_format": {"type": "json_object"}},
)
analysis_chain = prompt | llm | parser


async def analyze_query(query: str, history_text: str = "") -> QueryAnalysisResult | None:
    if not query.strip():
        return None

    try:
        result = await analysis_chain.ainvoke({
            "query": query,
            "history": history_text.strip() or "(없음)",
        })
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

    # 분해 서브쿼리 정제: 유효 dataset만, 빈 query 제외, (query,dataset) 중복 제거, 개수 제한.
    sub_queries: List[SubQuery] = []
    seen_pairs: set[tuple[str, str]] = set()
    for sq in result.sub_queries:
        q = (sq.query or "").strip()
        if not q or sq.dataset not in VALID_DATASETS:
            continue
        key = (q, sq.dataset)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        sub_queries.append(SubQuery(query=q, dataset=sq.dataset))
        if len(sub_queries) >= RAG_MAX_SUBQUERIES:
            break
    is_compound = bool(result.is_compound and sub_queries)

    return QueryAnalysisResult(
        normalized_question=result.normalized_question.strip() or query.strip(),
        intent=intent,
        entities=result.entities or {},
        time_focus=time_focus,
        search_queries=search_queries,
        needs_clarification=result.needs_clarification,
        clarification_reason=result.clarification_reason,
        is_compound=is_compound,
        sub_queries=sub_queries,
    )


__all__ = ["QueryAnalysisResult", "SubQuery", "analyze_query"]
