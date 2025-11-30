from typing import Dict

# 학생들이 자주 사용하는 은어/줄임말과 그에 해당하는 공식 용어를 매핑한 사전
# 필요에 따라 이 사전을 확장해나가면 됩니다.
SYNONYM_MAP: Dict[str, str] = {
    "드랍": "수강신청 취소",
    "학고": "학사경고",
    "학점포기": "성적포기",
    "계절학기": "계절수업",
    "칼졸업": "조기졸업",
    "공결": "공식 결석",
    "팀플": "팀 프로젝트",
    "도서관": "중앙도서관",
    "수강정정": "수강신청 정정",
    # 추가적인 동의어는 여기에 추가하세요.
    # 예:
    # "강의계획서": "교과목 해설서",
    # "자퇴": "자진퇴학",
}

def expand_query(query: str) -> str:
    """
    주어진 쿼리 문자열에 대해 사전에 정의된 은어/줄임말을 공식 용어로 확장합니다.
    """
    expanded_query = query
    for slang, formal_term in SYNONYM_MAP.items():
        # 대소문자 구분 없이, 단어 경계(word boundary)를 고려하여 치환
        # (예: "드랍"이 "드랍백"의 일부가 아닌 완전한 단어로 매치되도록)
        expanded_query = expanded_query.replace(slang, formal_term)
        # re.sub(r'\b' + re.escape(slang) + r'\b', formal_term, expanded_query, flags=re.IGNORECASE)

    return expanded_query

