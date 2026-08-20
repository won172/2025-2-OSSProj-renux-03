"""사용자 질문을 검색할 데이터셋으로 라우팅한다.

라우팅은 **LLM을 쓰지 않는다.** `api/rag_service._resolve_retrieval_route`가
질의분석이 뽑은 intent와 여기 있는 결정적 키워드 규칙을 합쳐 결정하고, 그 위에
안전 보강 규칙(일정 질문에 schedule 추가 등)을 얹는다. 라우팅 단계 지연은 실측
p50 0ms다.

한때 이 파일에 LLM 라우터(`route_query`)가 있었지만 호출부가 한 곳도 없었다.
질의분석이 intent를 이미 내주므로 같은 일을 하는 LLM 호출이 하나 더 필요하지
않았고, 그 사실이 코드에는 반영되지 않은 채 남아 있었다. 지연의 95%가 순차 LLM
호출인 시스템에서 쓰지 않는 LLM 경로를 남겨 두면, 다음 사람이 그것이 동작한다고
믿고 손대게 된다. 이력은 git에 있다.
"""
from __future__ import annotations

import re
from typing import List

# 질문에 이 어휘가 보이면 해당 데이터셋을 검색 대상에 넣는다. 여러 개가 걸리면
# 모두 넣는다 — 어느 하나로 좁히는 판단은 호출부(`_resolve_retrieval_route`)가 한다.
_KEYWORD_RULES: list[tuple[str, list[str]]] = [
    (r"학식|식단|학생식당|상록원|솥앤누들|누리터|d-?flex|디플렉스|메뉴|중식|석식", ["meals"]),
    (r"전화|연락처|내선|사무실|행정실|담당|교수|신고처|문의처|대표번호|이메일", ["staff"]),
    (r"수강신청|취소|재수강|휴학|복학|성적|졸업|학칙|규정|시행세칙|전과|복수전공", ["rules"]),
    (r"개강|종강|시험|일정|기간|학사일정|이번 주|이번 달", ["schedule"]),
    (r"교과|교과목|전공과목|이수구분|선수과목|학점|커리큘럼|교육과정|수업|강의", ["courses"]),
    (
        r"공지|장학|모집|발표|등록금|입시|채용|신청|"
        r"교내|캠퍼스|시설|도서관|열람실|와이파이|분실물|셔틀|프린터|"
        r"학생증|기숙사|생활관|식권|편의점|운영시간|공부\s*공간|학습\s*공간",
        ["notices"],
    ),
]


def keyword_route(query: str) -> List[str]:
    """질문 어휘로 검색 대상 데이터셋을 결정한다.

    아무것도 걸리지 않으면 `notices`로 둔다. 공지가 가장 넓은 코퍼스라 미분류
    질문의 답이 있을 확률이 가장 높다. 호출부는 이 기본값을 그대로 믿지 않고
    실제 공지 어휘가 있는지 따로 확인한다(`_can_skip_query_analysis`).
    """
    normalized = query.lower()
    routes: list[str] = []
    for pattern, names in _KEYWORD_RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            for name in names:
                if name not in routes:
                    routes.append(name)
    return routes or ["notices"]


def _keyword_route(query: str) -> List[str]:
    """예전 호출부 이름을 보존한다."""
    return keyword_route(query)


__all__ = ["keyword_route"]
