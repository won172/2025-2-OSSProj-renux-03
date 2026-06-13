"LLM을 사용하여 사용자의 질문을 가장 관련 있는 데이터셋으로 라우팅합니다."
from __future__ import annotations

import logging
import re
import time
from typing import List

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field, ValidationError
from langchain_openai import ChatOpenAI

from src.config import (
    LLM_ROUTER_DESCRIPTIONS,
    OPENAI_MODEL,
    OPENAI_CHAT_TIMEOUT_SECONDS,
    RAG_ROUTER_CACHE_TTL_SECONDS,
)

# LLM이 출력할 라우팅 결정의 스키마를 정의합니다.
# 여러 데이터셋과 관련될 수 있으므로 문자열 리스트를 사용합니다.
class RouteChoice(BaseModel):
    """사용자 질문에 가장 관련 있는 데이터셋(들)의 이름."""
    names: List[str] = Field(
        description="선택된 데이터셋의 이름 리스트. 예를 들어 ['rules', 'schedule']와 같이 출력되어야 합니다."
    )

# LangChain V2부터 PydanticOutputParser를 사용하는 것이 권장됩니다.
parser = PydanticOutputParser(pydantic_object=RouteChoice)

# LLM에게 제공할 프롬프트 템플릿입니다.
# 사용자의 질문과 각 데이터셋의 설명을 제공하고, 가장 적절한 것을 선택하도록 요청합니다.
prompt = PromptTemplate(
    template="""사용자의 질문을 분석하여 가장 관련 있는 데이터셋으로 라우팅하는 역할을 수행합니다.
질문에 답변하기 위해 참조해야 할 가장 적절한 데이터셋을 선택하세요. 특별한 이유가 없는 한 하나만 선택하세요.

사용 가능한 데이터셋:
{destinations}

라우팅 기준:
- notices: 장학금, 모집, 발표, 일반 공지, 입학 공지, 최신 공지
- rules: 수강신청 취소, 재수강, 휴학, 복학, 성적, 졸업, 학칙, 규정, 시행세칙
- schedule: 개강, 종강, 시험, 수강신청 기간, 학사일정, 이번 주/이번 달 일정
- staff: 전화번호, 내선, 연락처, 사무실, 담당 부서
- courses: 교과과정, 교과목, 이수구분, 전공과목, 선수과목
- meals: 학식, 학생식당, 상록원, 솥앤누들, 누리터, 경영관, D-Flex, 디플렉스, 식단, 메뉴, 오늘/이번주 뭐 먹지, 중식/석식

헷갈리기 쉬운 예시:
- "수강신청 취소는 어떻게 해?" -> ["rules"]
- "이번 주 학사일정 알려줘" -> ["schedule"]
- "컴퓨터공학과 사무실 전화번호 알려줘" -> ["staff"]
- "통계학과 교과과정 알려줘" -> ["courses"]
- "장학금 신청 기간이 언제야?" -> ["notices"]
- "오늘 학식 뭐 나와?" -> ["meals"]
- "상록원 2층 이번 주 메뉴 알려줘" -> ["meals"]

추가 지침:
- 전화번호나 연락처를 묻는 질문은 반드시 staff를 우선 선택하세요.
- 학칙, 규정, 신청 절차, 취소/변경 규정 질문은 반드시 rules를 우선 선택하세요.
- 일정, 기간, 개강, 종강, 시험 날짜 질문은 schedule을 우선 선택하세요.
- 공지의 내용, 모집, 장학, 발표, 최신 공지는 notices를 우선 선택하세요.
- 학식/식단/메뉴/학생식당(상록원·솥앤누들·누리터) 질문은 반드시 meals를 선택하세요.
- 여러 데이터셋이 모두 필요할 때만 복수 선택하세요. 그렇지 않으면 가장 강한 1개만 선택하세요.

사용자 질문:
{query}

선택된 데이터셋의 이름을 포함하는 JSON 객체를 다음 형식으로 출력하세요:
{format_instructions}
""",
    input_variables=["query", "destinations"],
    partial_variables={"format_instructions": parser.get_format_instructions()},
)

# 라우팅을 수행할 LLM 체인을 구성합니다.
# 파싱은 별도로 수행해 실패 시 LLM 원본 출력을 로깅할 수 있게 한다(디버깅 용이).
llm = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0,
    timeout=OPENAI_CHAT_TIMEOUT_SECONDS,
    max_retries=1,
    model_kwargs={"response_format": {"type": "json_object"}},
)
router_chain = prompt | llm
_route_cache: dict[str, tuple[float, List[str]]] = {}


_KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (r"학식|식단|학생식당|상록원|솥앤누들|누리터|d-?flex|디플렉스|메뉴|중식|석식", ["meals"]),
    (r"전화|연락처|내선|사무실|행정실|담당|교수", ["staff"]),
    (r"수강신청|취소|재수강|휴학|복학|성적|졸업|학칙|규정|시행세칙|전과|복수전공", ["rules"]),
    (r"개강|종강|시험|일정|기간|학사일정|이번 주|이번 달", ["schedule"]),
    (r"교과|교과목|전공과목|이수구분|선수과목|학점|커리큘럼|교육과정|수업|강의", ["courses"]),
    (r"공지|장학|모집|발표|등록금|입시|채용|신청", ["notices"]),
]

def _format_destinations() -> str:
    """LLM 프롬프트에 포함될 데이터셋 설명의 형식을 지정합니다."""
    return "\n".join(f"- {name}: {desc}" for name, desc in LLM_ROUTER_DESCRIPTIONS.items())


def _keyword_route(query: str) -> List[str]:
    """LLM 라우터 장애 시 최소한의 의도 보존을 위한 deterministic 폴백."""
    normalized = query.lower()
    routes: list[str] = []
    for pattern, names in _KEYWORD_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            for name in names:
                if name not in routes:
                    routes.append(name)
    return routes or ["notices"]


def _cached_route(query: str) -> List[str] | None:
    if RAG_ROUTER_CACHE_TTL_SECONDS <= 0:
        return None
    cached = _route_cache.get(query)
    if cached is None:
        return None
    expires_at, route = cached
    if expires_at < time.monotonic():
        _route_cache.pop(query, None)
        return None
    return list(route)


def _store_route(query: str, route: List[str]) -> None:
    if RAG_ROUTER_CACHE_TTL_SECONDS <= 0:
        return
    _route_cache[query] = (time.monotonic() + RAG_ROUTER_CACHE_TTL_SECONDS, list(route))

async def route_query(query: str) -> List[str]:
    """LLM 라우터를 사용하여 사용자 질문에 가장 적합한 데이터셋을 결정합니다."""
    if not query:
        # 질문이 비어 있으면 기본값으로 'notices'를 반환합니다.
        return ["notices"]

    cached = _cached_route(query)
    if cached is not None:
        return cached

    formatted_destinations = _format_destinations()
    
    raw_text = None
    try:
        raw_message = await router_chain.ainvoke({
            "query": query,
            "destinations": formatted_destinations,
        })
        raw_text = getattr(raw_message, "content", None) or str(raw_message)

        try:
            result = parser.parse(raw_text)
        except (ValidationError, OutputParserException) as e:
            # 스키마 불일치/엉뚱한 출력을 디버깅할 수 있도록 LLM 원본 출력을 함께 남긴다.
            logging.warning(
                "LLM 라우터 출력 파싱에 실패했습니다: %s | 원본 출력=%r. 'notices'로 기본 설정합니다.",
                e, raw_text,
            )
            route = _keyword_route(query)
            _store_route(query, route)
            return route

        # LLM의 선택에서 유효한 데이터셋 이름만 필터링합니다.
        # LLM이 목록에 없는 이름을 지어낼 경우를 대비합니다.
        valid_routes = [name for name in result.names if name in LLM_ROUTER_DESCRIPTIONS]
        if not valid_routes:
            logging.warning(
                "LLM 라우터가 유효한 데이터셋을 반환하지 않았습니다. 원본 출력=%r. 'notices'로 기본 설정합니다.",
                raw_text,
            )
        route = valid_routes or _keyword_route(query)
        _store_route(query, route)
        return route
    except Exception as e:
        route = _keyword_route(query)
        logging.error(
            "LLM 라우터에서 예기치 않은 오류가 발생했습니다: %s | 원본 출력=%r. 키워드 폴백 route=%s.",
            e, raw_text, route,
        )
        _store_route(query, route)
        return route

# 이 파일에서 외부에 제공할 함수는 route_query 뿐입니다.
__all__ = ["route_query"]
