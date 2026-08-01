"""검색 직전 질의 표기 정규화 — 오타·속어·부서명 별칭.

로그에서 확인한 실패 유형을 다룬다.

- 오타·속어: "등럭금"(등록금)·"긱사"(기숙사)가 그대로 검색어로 나가 매칭 실패
- 부서명 별칭: staff 데이터셋으로 정확히 라우팅되고도 "학과사무실"이라는 표현이
  표의 "행정실"·"교학팀" 표기와 만나지 못해 연락처 질문 4건이 전부 실패

원문을 바꾸지 않는다. 검색용 질의를 하나 더 만들어 후보를 넓히는 용도다
(사용자에게 보여주는 질문·답변은 원문 기준으로 유지된다).
"""
from __future__ import annotations

import re

# 오타·구어체 → 표준 표기. 로그에 실제로 등장한 것만 담는다.
# 무리하게 넓히면 엉뚱한 치환이 생기므로 확인된 것만 추가한다.
TYPO_CORRECTIONS: dict[str, str] = {
    "긱사": "기숙사",
    "기사비": "기숙사비",
    "등럭금": "등록금",
    "등록끔": "등록금",
    "장학긍": "장학금",
    "졸업요껀": "졸업요건",
    "수강신챠": "수강신청",
    "휴학신청서": "휴학 신청",
    "학샤일정": "학사일정",
    "학사알정": "학사일정",
    "시간표짜": "시간표 작성",
    "교환학생신청": "교환학생 지원",
}

# 학교 원문과 사용자 표현에서 실제로 어긋난 복합명사 띄어쓰기. 이 목록은 의미를
# 확장하지 않고 표기만 맞추므로 단일 검색 모드에서도 안전하게 교정본을 사용한다.
SPACING_NORMALIZATIONS: dict[str, str] = {
    "수강 신청": "수강신청",
    "학사 일정": "학사일정",
    "희망 강의": "희망강의",
}

# 부서·시설 표현의 동의어. 검색어에 함께 넣어 표기 차이를 흡수한다.
DEPARTMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "학과사무실": ("행정실", "교학팀", "학과 행정실", "사무실"),
    "과사무실": ("행정실", "교학팀", "사무실"),
    "과사": ("행정실", "교학팀", "사무실"),
    "행정실": ("학과사무실", "교학팀", "사무실"),
    "교학팀": ("학과사무실", "행정실"),
    "학생지원팀": ("학생지원", "학생과", "학생팀"),
    "학사지원팀": ("학사팀", "학사과", "교무팀"),
    "교무팀": ("학사지원팀", "교무과"),
    "장학팀": ("장학과", "학생지원팀", "장학 담당"),
    "입학처": ("입학팀", "입학관리"),
    "국제처": ("국제교류팀", "국제팀", "국제교류"),
    "생활관": ("기숙사", "남산학사", "학사"),
    "기숙사": ("생활관", "남산학사"),
    "도서관": ("중앙도서관", "학술정보관"),
    "정보관리팀": ("정보전산원", "전산팀", "IT지원"),
}

# 연락처를 묻는 신호. 이 신호가 있을 때만 부서 별칭을 확장한다.
_CONTACT_SIGNAL = re.compile(r"연락처|전화|번호|내선|문의|어디에\s*전화|담당")


def correct_typos(query: str) -> str:
    """알려진 오타·속어를 표준 표기로 바꾼다. 해당 없으면 원문 그대로."""
    corrected = query
    for wrong, right in TYPO_CORRECTIONS.items():
        if wrong in corrected:
            corrected = corrected.replace(wrong, right)
    for spaced, compact in SPACING_NORMALIZATIONS.items():
        if spaced in corrected:
            corrected = corrected.replace(spaced, compact)
    return corrected


def expand_department_aliases(query: str) -> list[str]:
    """질문에 등장한 부서 표현의 동의어로 만든 추가 검색어들.

    연락처를 묻는 질문에만 적용한다 — "행정실 위치"처럼 다른 맥락에서
    동의어를 붙이면 검색이 오히려 흐려진다.
    """
    if not _CONTACT_SIGNAL.search(query):
        return []

    compact = query.replace(" ", "")
    variants: list[str] = []
    for canonical, aliases in DEPARTMENT_ALIASES.items():
        if canonical.replace(" ", "") not in compact:
            continue
        for alias in aliases:
            variant = query.replace(canonical, alias)
            if variant != query and variant not in variants:
                variants.append(variant)
    return variants


def normalize_query(query: str) -> tuple[str, list[str]]:
    """(오타 교정한 질의, 추가 검색어 목록)을 돌려준다.

    추가 검색어는 원문을 대체하지 않고 검색 후보를 넓히는 데만 쓴다.
    """
    corrected = correct_typos(query)
    extras: list[str] = []

    # 오타를 고쳤다면 교정본도 검색 후보에 넣는다(원문 표기가 맞을 수도 있으므로).
    if corrected != query:
        extras.append(corrected)

    for variant in expand_department_aliases(corrected):
        if variant not in extras:
            extras.append(variant)

    return corrected, extras


__all__ = [
    "DEPARTMENT_ALIASES",
    "TYPO_CORRECTIONS",
    "SPACING_NORMALIZATIONS",
    "correct_typos",
    "expand_department_aliases",
    "normalize_query",
]
