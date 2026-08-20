"""검색 전에 걸러내야 하는 발화 처리 — 스몰톡과 후속 발화.

로그 1,028건에서 확인한 두 가지 문제를 다룬다.

1. 스몰톡(96건): "안녕" 34회·"이름이 뭐니" 13회·"고마워" 7회가 RAG로 들어가
   "자료를 찾지 못했습니다"로 답했다. 첫인사에 실패 메시지를 주고 있었다.
2. 후속 발화: "그 강의 신청해도 돼?"·"거기 오늘 열어?"·"자세히 알려줘"처럼
   지시어가 그대로 검색어로 나가 매칭에 실패했다. 직전 대화로 보충해야 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------- 스몰톡

@dataclass(frozen=True)
class SmalltalkReply:
    """검색 없이 바로 내보낼 응답."""
    answer: str
    kind: str


BOT_NAME = "동똑이"

_GREETING = re.compile(r"^(안녕|안녕하세요|하이|헬로|hi|hello|ㅎㅇ|반가워|반갑습니다|좋은\s*아침)[!.~\s]*$", re.IGNORECASE)
_THANKS = re.compile(r"^(고마워|고맙습니다|감사|감사합니다|땡큐|thanks|thank\s*you|ㄱㅅ)[!.~\s]*$", re.IGNORECASE)
_BYE = re.compile(r"^(잘있어|안녕히|바이|bye|수고|잘가|다음에)[!.~\s]*$", re.IGNORECASE)
_IDENTITY = re.compile(r"(너|당신|넌)\s*(는|은)?\s*(누구|뭐야|뭐니)|이름\s*(이|은)?\s*(뭐|무엇)|정체가|어떤\s*(봇|챗봇|ai)")
_CAPABILITY = re.compile(r"(무엇|뭐)\s*(을|를)?\s*(할\s*수\s*있|도와|해줄)|어떤\s*(걸|것|기능)|사용법|뭐\s*할\s*수")
_ORIGIN = re.compile(r"언제\s*만들|누가\s*만들|개발자|만든\s*사람|생일")
_FEELING = re.compile(r"기분\s*(어때|어떄|좋아)|잘\s*지냈|밥\s*먹었|뭐\s*해")
# 단독 긍정/부정/추임새. 뒤에 실질 내용이 없을 때만 스몰톡으로 본다.
_ACK = re.compile(r"^(ㅇ+|ㅇㅋ|오케이|ok|응+|어+|네+|예+|그래|맞아|맞아요|좋아|좋아요|아니|아니요|ㄴㄴ|ㄴㅇ|알겠어|알겠습니다|야)[!.~\s]*$", re.IGNORECASE)

_IDENTITY_ANSWER = (
    f"저는 동국대학교 재학생을 돕는 AI 챗봇 **{BOT_NAME}**입니다.\n\n"
    "학사일정·공지사항·학칙·교과목·교직원 연락처·학식 정보를 학교 자료에서 찾아 "
    "출처와 함께 알려드려요. 궁금한 걸 편하게 물어보세요."
)

_CAPABILITY_ANSWER = (
    f"{BOT_NAME}는 이런 걸 도와드릴 수 있어요.\n\n"
    "- **학사일정**: 개강·종강·시험 기간·수강신청 일정\n"
    "- **공지사항**: 장학·모집·행사 등 최신 공지\n"
    "- **학칙·규정**: 휴학·복학·재수강·졸업요건\n"
    "- **교과목**: 학과별 교과과정·전공필수·이수구분\n"
    "- **연락처**: 학과 사무실·행정 부서\n"
    "- **학식**: 오늘의 식단과 코너별 메뉴\n\n"
    "예를 들어 \"이번 학기 종강일 언제야?\"처럼 물어보세요."
)


def detect_smalltalk(query: str) -> SmalltalkReply | None:
    """검색이 필요 없는 발화를 골라 바로 쓸 응답을 만든다.

    짧은 발화만 대상으로 한다 — "안녕하세요, 졸업요건 알려주세요"처럼 인사에
    실제 질문이 붙은 경우는 검색으로 보내야 하기 때문이다.
    """
    text = (query or "").strip()
    if not text:
        return None

    # 긴 문장은 인사말이 섞여 있어도 실질 질문으로 취급한다.
    if len(text) > 20:
        return None

    if _GREETING.match(text):
        return SmalltalkReply(
            f"안녕하세요! 동국대학교 챗봇 {BOT_NAME}입니다.\n\n"
            "학사일정·공지·학칙·교과목·연락처·학식을 찾아드릴 수 있어요. 무엇을 도와드릴까요?",
            "greeting",
        )
    if _THANKS.match(text):
        return SmalltalkReply("도움이 되었다면 다행이에요. 더 궁금한 게 있으면 언제든 물어보세요.", "thanks")
    if _BYE.match(text):
        return SmalltalkReply("필요할 때 다시 찾아주세요. 좋은 하루 보내세요!", "bye")
    if _IDENTITY.search(text):
        return SmalltalkReply(_IDENTITY_ANSWER, "identity")
    if _CAPABILITY.search(text):
        return SmalltalkReply(_CAPABILITY_ANSWER, "capability")
    if _ORIGIN.search(text):
        return SmalltalkReply(
            f"{BOT_NAME}는 동국대학교 융합소프트웨어 연계전공 학생들이 만든 학사 정보 챗봇입니다. "
            "학교 공지·학칙 자료를 근거로 답하도록 만들어졌어요.",
            "origin",
        )
    if _FEELING.search(text):
        return SmalltalkReply(
            "저는 언제든 준비되어 있어요. 학사일정이나 공지처럼 학교 정보가 필요하면 말씀해 주세요.",
            "feeling",
        )
    if _ACK.match(text):
        return SmalltalkReply(
            "네, 더 궁금한 게 있으면 이어서 물어보세요. 예를 들어 \"이번 주 학사일정 알려줘\"처럼요.",
            "ack",
        )
    return None


# ---------------------------------------------------------------- 후속 발화

# 앞 대화를 가리키는 지시어·생략 표현.
_FOLLOW_UP_MARKERS = re.compile(
    r"그거|그것|그건|그\s|거기|저기|이거|이것|아까|방금|위에|해당|그때|그런|그럼|"
    r"자세히|더\s*알려|구체적으로|예시|다시|또"
)

# 이것만으로는 검색할 수 없는 짧은 발화 길이.
_SHORT_UTTERANCE_LEN = 12

_LEXICAL_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
_LEXICAL_STOPWORDS = {
    "그", "이", "저", "것", "거", "좀", "더", "또", "다시", "아까", "방금",
    "오늘", "지금", "현재", "요즘", "언제", "어디", "뭐", "무엇", "누구",
    "알려줘", "알려주세요", "말해줘", "보여줘", "해줘", "해주세요", "자세히",
    "가능", "관련", "대한", "대해", "질문", "정보",
}
_KOREAN_PARTICLE_SUFFIXES = (
    "으로부터", "에서부터", "에게서", "이라도", "이라면", "이라고", "에서는",
    "으로", "에서", "에게", "한테", "보다", "처럼", "까지", "부터", "라도",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만",
)


def _normalize_lexical_token(token: str) -> str:
    normalized = token.lower().strip()
    for suffix in _KOREAN_PARTICLE_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) >= len(suffix) + 2:
            return normalized[: -len(suffix)]
    return normalized


def meaningful_lexical_terms(text: str) -> set[str]:
    """Return stable topic-bearing terms for conservative follow-up checks."""
    terms = {
        _normalize_lexical_token(token)
        for token in _LEXICAL_TOKEN.findall(str(text or ""))
    }
    return {
        term
        for term in terms
        if len(term) >= 2 and term not in _LEXICAL_STOPWORDS
    }


def has_lexical_overlap(left: str, right: str) -> bool:
    """Whether two utterances share a meaningful topic term.

    Substring comparison tolerates Korean particles and compounds such as
    ``공모전``/``공모전은`` while excluding generic follow-up wording.
    """
    left_terms = meaningful_lexical_terms(left)
    right_terms = meaningful_lexical_terms(right)
    return any(
        left_term in right_term or right_term in left_term
        for left_term in left_terms
        for right_term in right_terms
    )


def history_allows_context_rewrite(query: str, history_text: str) -> bool:
    """Allow history only when the current and previous user topics overlap."""
    previous = extract_last_user_question(history_text)
    return bool(previous and has_lexical_overlap(query, previous))


def preserve_original_query(query: str, candidate: str) -> str | None:
    """Make a relevant rewrite additive; reject unrelated replacements.

    A candidate must share at least one meaningful term with the original.
    The exact original utterance is prepended when the model normalized it
    away, so downstream retrieval and generation can never lose user tokens.
    """
    original = str(query or "").strip()
    rewritten = str(candidate or "").strip()
    if not original or not rewritten:
        return original or None
    compact_original = re.sub(r"\s+", "", original).lower()
    compact_rewritten = re.sub(r"\s+", "", rewritten).lower()
    if compact_original in compact_rewritten:
        return rewritten
    if not has_lexical_overlap(original, rewritten):
        return None
    return f"{original} {rewritten}".strip()


def needs_context_rewrite(query: str, history_text: str) -> bool:
    """직전 대화로 보충해야 검색이 가능한 발화인가."""
    text = (query or "").strip()
    if not text or not (history_text or "").strip():
        return False
    # 스몰톡은 검색 대상이 아니므로 재구성하지 않는다.
    if detect_smalltalk(text) is not None:
        return False
    # 짧다는 이유만으로 직전 주제를 상속하지 않는다. 현재 발화와 직전 사용자
    # 질문 사이에 실질 어휘가 없으면 새 주제로 취급한다.
    if not history_allows_context_rewrite(text, history_text):
        return False
    if _FOLLOW_UP_MARKERS.search(text):
        return True
    # 지시어가 없어도 너무 짧으면 단독 검색이 어렵다("시험 언제 봐?" 수준은 통과).
    return len(text) <= _SHORT_UTTERANCE_LEN and "?" not in text and not _has_content_noun(text)


_CONTENT_NOUNS = re.compile(
    r"학사일정|일정|공지|장학|등록금|학식|시험|개강|종강|수강신청|휴학|복학|재수강|"
    r"졸업|학점|교과|전공|연락처|전화|사무실|기숙사|도서관|성적|방학|계절학기"
)


def _has_content_noun(text: str) -> bool:
    return bool(_CONTENT_NOUNS.search(text))


def extract_last_user_question(history_text: str) -> str | None:
    """대화 이력 문자열에서 직전 사용자 질문을 뽑는다.

    이력은 "사용자: ...\\n동똑이: ..." 형태로 누적된다.
    형식이 달라져도 마지막 사용자 줄을 최대한 찾아내되, 못 찾으면 None을 준다.
    """
    if not history_text:
        return None

    candidates: list[str] = []
    for line in history_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for prefix in ("사용자:", "user:", "User:", "Human:", "질문:"):
            if stripped.startswith(prefix):
                content = stripped[len(prefix):].strip()
                if content:
                    candidates.append(content)
                break

    return candidates[-1] if candidates else None


def rewrite_with_context(query: str, history_text: str) -> str:
    """후속 발화를 직전 질문과 합쳐 단독 검색 가능한 형태로 만든다.

    LLM 질의 분석이 이미 같은 일을 하지만, 분석이 꺼져 있거나 실패했을 때를 위한
    결정적 안전망이다. 원문을 버리지 않고 앞에 맥락을 덧붙이기만 한다.
    """
    previous = extract_last_user_question(history_text)
    if not previous or not has_lexical_overlap(query, previous):
        return query
    if previous.strip() == query.strip():
        return query
    return f"{previous} {query}".strip()


__all__ = [
    "BOT_NAME",
    "SmalltalkReply",
    "detect_smalltalk",
    "extract_last_user_question",
    "has_lexical_overlap",
    "history_allows_context_rewrite",
    "meaningful_lexical_terms",
    "needs_context_rewrite",
    "preserve_original_query",
    "rewrite_with_context",
]
