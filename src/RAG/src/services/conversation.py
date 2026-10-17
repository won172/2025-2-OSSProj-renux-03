"""검색 전에 걸러내야 하는 발화 처리 — 스몰톡과 후속 발화.

로그 1,028건에서 확인한 두 가지 문제를 다룬다.

1. 스몰톡(96건): "안녕" 34회·"이름이 뭐니" 13회·"고마워" 7회가 RAG로 들어가
   "자료를 찾지 못했습니다"로 답했다. 첫인사에 실패 메시지를 주고 있었다.
2. 후속 발화: "그 강의 신청해도 돼?"·"거기 오늘 열어?"·"자세히 알려줘"처럼
   지시어가 그대로 검색어로 나가 매칭에 실패했다. 직전 대화로 보충해야 한다.
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import Literal

from src.utils.query_years import extract_explicit_years


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
    "문서", "자료", "내용", "기간", "방법", "신청",
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
    """Explicit references may omit nouns; standalone topics still need overlap."""
    previous = extract_last_user_question(history_text)
    return bool(previous and detect_smalltalk(query) is None and (
        has_lexical_overlap(query, previous) or _explicit_reference(query)
    ))


def _explicit_reference(query: str) -> bool:
    text = query.strip()
    return bool(
        re.search(r"(?:그|이|저|해당)\s*(?:문서|자료|공지|규정|과목|강의|장학금|공모전|프로그램|일정)|"
                  r"그거|그것|그건|거기|그때|아까|방금|위에서", text)
        or re.fullmatch(r"(?:그럼\s*|그러면\s*)?(?:좀\s*)?(?:자세히|더|구체적으로|다시)\s*"
                        r"(?:알려\s*줘|알려주세요|설명해\s*줘|보여\s*줘)[?.!\s]*", text)
        or re.fullmatch(r"(?:그럼|그러면)\s*(?:신청\s*)?(?:기간|방법|조건|대상|마감|언제|어디|비용|내용).*", text)
        or re.match(r"(?:아니[,\s]*|정정[,\s]*)(?:\d{2,4}\s*(?:학번|학년도|년)|.*(?:말고|아니라|기준))", text)
        or _SLOT_FOLLOWUP.fullmatch(text)
        or _document_numbers(text) is not None
        or _generic_document_reference(text)
        or (re.search(r"말고|아니라|아니고", text) and any(_scopes(text).values()))
    )


_SLOT_FOLLOWUP = re.compile(
    r"(?:(?:그럼|그러면)\s*)?(?:(?:신청|접수|지원|이수|수강)\s*)?"
    r"(?:조건|기간|마감|대상|자격|방법|절차|비용|장소|제출\s*서류)"
    r"(?:은|는|도|이|가)?\s*(?:언제야|언제|어떻게|뭐야|알려줘|알려주세요)?[?.!\s]*"
)
_YEAR_SCOPE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2}(?=\s*(?:학번|학년도|[-./]\s*[12])))"
    r"(?:\s*(?:학번|학년도|년)|[-./]\s*[12](?:학기)?)?(?!\d)"
)
_CAMPUS_SCOPE = re.compile(
    r"(?:WISE|와이즈|경주|서울|바이오메디|바이오메디컬|BMC)\s*(?:캠퍼스)?", re.IGNORECASE
)
_MAJOR_SCOPE = re.compile(r"[가-힣A-Za-z·]+(?:학과|학부|전공)")
_SEMESTER_SCOPE = re.compile(
    r"(?<!\d)(?:[12]\s*학기|[하동]계(?:\s*계절학기)?|여름(?:\s*계절학기)?|겨울(?:\s*계절학기)?|계절학기)"
)


def effective_scope_query(query: str) -> str:
    """Exclude explicitly negated values when deriving filters, keeping raw text elsewhere."""
    return re.sub(r"\S+\s*(?:말고|아니라|아니고)\s*", "", query)


def _scopes(text: str) -> dict[str, set]:
    text = effective_scope_query(text)
    campus_aliases = {"서울": "seoul", "와이즈": "wise", "경주": "wise",
                      "바이오메디": "bmc", "바이오메디컬": "bmc"}
    campuses = {re.sub(r"\s+|캠퍼스", "", value).lower() for value in _CAMPUS_SCOPE.findall(text)}
    return {
        "years": extract_explicit_years(text),
        "semester": {_semester(value) for value in _SEMESTER_SCOPE.findall(text)} | {
            value + "학기" for value in re.findall(r"\b(?:20\d{2}|\d{2})\s*[-./]\s*([12])(?!\d)", text)
        },
        "campus": {campus_aliases.get(value, value) for value in campuses},
        "major": set(_MAJOR_SCOPE.findall(text)),
    }


def _semester(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    if compact.startswith(("하계", "여름")):
        return "summer"
    if compact.startswith(("동계", "겨울")):
        return "winter"
    return compact


def _document_numbers(query: str) -> list[int] | None:
    numbers = re.findall(r"\[\s*문서\s*(\d+)\s*\]|(?<![0-9A-Za-z가-힣])문서\s*(\d+)\s*번", query)
    result = [int(left or right) for left, right in numbers]
    ordinals = {"첫": 1, "첫째": 1, "두": 2, "둘째": 2, "세": 3, "셋째": 3,
                "네": 4, "넷째": 4, "다섯": 5, "여섯": 6}
    # An ordinal is a token, never a syllable inside 참고한/확인한/연구세.
    for word in re.findall(r"(?<![0-9A-Za-z가-힣])(첫째|둘째|셋째|넷째|첫|두|세|네|다섯|여섯|\d+)"
                           r"\s*(?:번째|번)\s*(?:문서|자료)", query):
        result.append(int(word) if word.isdigit() else ordinals[word])
    return list(dict.fromkeys(result)) if result else None


def _generic_document_reference(query: str) -> bool:
    if re.search(r"(?:문서|자료)들|(?:모든|전체|각)\s*(?:문서|자료)", query):
        return False
    return bool(re.search(
        r"(?<![0-9A-Za-z가-힣])(?:그|이|저|해당|이전(?:에)?|앞서|아까|방금)"
        r"(?:\s+|(?=문서|자료|참고|확인)).*?(?:문서|자료)", query
    ))


def _history_turns(history_text: str) -> list[dict]:
    turns: list[dict] = []
    for line in history_text.splitlines():
        question = extract_last_user_question(line)
        if question:
            turns.append({"question": question, "sources": [], "lines": [line]})
        elif turns:
            turns[-1]["lines"].append(line)
            if line.startswith("이전 출처(검색 단서): "):
                try:
                    cue = json.loads(line.split(": ", 1)[1])
                    if isinstance(cue, dict):
                        turns[-1]["sources"].append(cue)
                except (ValueError, TypeError):
                    pass
    return turns


def _referenced_thread(query: str, history_text: str) -> list[dict]:
    if not history_allows_context_rewrite(query, history_text):
        return []
    turns = _history_turns(history_text)
    if not turns:
        return []
    end = len(turns)
    # A named older document can be referenced beyond the old three-turn window.
    if _FOLLOW_UP_MARKERS.search(query) and _document_numbers(query) is None:
        for index in range(end - 1, -1, -1):
            turn = turns[index]
            if any(has_lexical_overlap(query, str(cue.get("title", ""))) for cue in turn["sources"]):
                end = index + 1
                break
    start = end - 1
    while start > 0 and (detect_smalltalk(turns[start]["question"]) or _explicit_reference(turns[start]["question"])):
        start -= 1
    return turns[start:end]


def _resolved_context(query: str, history_text: str) -> tuple[list[str], list[dict], bool]:
    turns = _referenced_thread(query, history_text)
    effective = {"years": set(), "semester": set(), "campus": set(), "major": set()}
    for text in [turn["question"] for turn in turns] + [query]:
        for kind, values in _scopes(text).items():
            if values:
                effective[kind] = values

    def clean(text: str) -> str:
        # Remove a whole superseded constraint, including its suffix. Never leave
        # an orphan '학번'/'학년도', and never treat a title as permission for a year.
        text = effective_scope_query(text)
        def clean_year(match):
            if not extract_explicit_years(match.group()) <= effective["years"]:
                return ""
            compact = re.fullmatch(r"(20\d{2}|\d{2})\s*[-./]\s*([12])(?:학기)?", match.group())
            if compact and effective["semester"] and compact.group(2) + "학기" not in effective["semester"]:
                year = int(compact.group(1))
                return f"{year if year >= 2000 else year + 2000}학년도"
            return match.group()
        text = _YEAR_SCOPE.sub(clean_year, text)
        for pattern, kind in ((_SEMESTER_SCOPE, "semester"), (_CAMPUS_SCOPE, "campus"), (_MAJOR_SCOPE, "major")):
            if effective[kind]:
                text = pattern.sub(lambda m: m.group() if _scopes(m.group())[kind] <= effective[kind] else "", text)
        return " ".join(text.split())

    questions = [clean(turn["question"]) for turn in turns if not detect_smalltalk(turn["question"])]
    sources = next((turn["sources"] for turn in reversed(turns) if turn["sources"]), [])
    numbers = _document_numbers(query)
    if numbers is not None:
        # Citation numbers belong to the latest referenced answer. Missing or
        # duplicated labels cannot safely select a document.
        matches = [cue for cue in sources if cue.get("citation_number") in numbers]
        if len(numbers) != 1 or len(matches) != 1:
            return questions, [], True
        sources = matches
        # A multi-document original question must not pull source 1 back into a
        # request for source 2. The chosen title is the discovery query instead.
        questions = []
    else:
        selections = [index for index, turn in enumerate(turns) if _document_numbers(turn["question"]) is not None]
        if selections:
            questions = [clean(turn["question"]) for turn in turns[selections[-1]:]]
        if _FOLLOW_UP_MARKERS.search(query):
            named = [cue for cue in sources if _names_source(query, [cue])]
            if named:
                longest = max(len(re.sub(r"\s+", "", str(cue.get("title", "")))) for cue in named)
                named = [cue for cue in named if len(re.sub(r"\s+", "", str(cue.get("title", "")))) == longest]
            else:
                named = [cue for cue in sources if has_lexical_overlap(query, str(cue.get("title", "")))]
            if len(named) == 1:
                sources, questions = named, []
    safe_sources = []
    for cue in sources:
        # Retain identity only when its title does not contradict newer scope.
        title = str(cue.get("title", ""))
        scopes = _scopes(title)
        if any(effective[kind] and values and not values <= effective[kind]
               for kind, values in scopes.items()):
            continue
        campus = str(cue.get("campus_scope", "")).lower()
        if effective["campus"] and campus in {"seoul", "bmc", "wise"} and campus not in effective["campus"]:
            continue
        safe_sources.append({**cue, "title": clean(title)})
    # A document selection replaces the multi-document topic, but user-supplied
    # scope must survive that selection and any later noun-free follow-up.
    constraints = []
    for turn in turns:
        for pattern in (_YEAR_SCOPE, _SEMESTER_SCOPE, _CAMPUS_SCOPE, _MAJOR_SCOPE):
            constraints.extend(clean(match.group()) for match in pattern.finditer(turn["question"]))
    scope_text = " ".join(dict.fromkeys(value for value in constraints if value))
    if scope_text and not any(scope_text in question for question in questions):
        questions.append(scope_text)
    documents = {cue.get("document_key") or cue.get("doc_id") or cue.get("url") or cue.get("title")
                 or cue.get("chunk_id") or cue.get("source_ref") for cue in safe_sources}
    ambiguous = (numbers is not None and not safe_sources) or (
        _generic_document_reference(query) and (len(documents) > 1 or bool(sources and not safe_sources))
    )
    return questions, safe_sources, bool(ambiguous)


def followup_source_clarification(query: str, history_text: str) -> bool:
    if _document_numbers(query) is None and not _generic_document_reference(query):
        return False
    if not history_allows_context_rewrite(query, history_text):
        return True
    _, sources, ambiguous = _resolved_context(query, history_text)
    # An identified source must never fall back to title-only discovery when
    # its dataset/identity is malformed or unsupported.
    return ambiguous or any(
        any(cue.get(key) for key in ("source", "dataset", "document_key", "doc_id", "chunk_id"))
        and _typed_source_reference(cue) is None for cue in sources
    )


SourceDataset = Literal["notices", "rules", "schedule", "staff", "courses", "meals"]
_SOURCE_DOMAIN_HINTS: dict[SourceDataset, str] = {
    "notices": "학교 공지사항", "rules": "학교 학칙 규정", "schedule": "학교 학사일정",
    "staff": "학교 교직원 연락처", "courses": "학교 교과목", "meals": "학교 학식",
}


@dataclass(frozen=True)
class FollowupSourceReference:
    dataset: SourceDataset
    document_key: str | None = None
    chunk_id: str | None = None

    @property
    def domain_hint(self) -> str:
        return _SOURCE_DOMAIN_HINTS[self.dataset]


def _typed_source_reference(cue: dict) -> FollowupSourceReference | None:
    def bounded(key: str) -> str | None:
        value = cue.get(key)
        return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 256 else None
    document_key = bounded("document_key") or bounded("doc_id")
    dataset = bounded("source") or bounded("dataset")
    if dataset is None and document_key:
        dataset = document_key.split(":", 1)[0]
    if dataset not in _SOURCE_DOMAIN_HINTS:
        return None
    if document_key and not document_key.startswith(f"{dataset}:"):
        return None
    chunk_id = bounded("chunk_id")
    if not document_key and not chunk_id:
        return None
    return FollowupSourceReference(dataset, document_key, chunk_id)


def followup_source_reference(query: str, history_text: str) -> FollowupSourceReference | None:
    """Resolve one referenced source as a typed retrieval scope, never as facts."""
    if not needs_context_rewrite(query, history_text):
        return None
    _, sources, ambiguous = _resolved_context(query, history_text)
    # A scope correction asks for new evidence; it cannot pin an older doc
    # merely because the title omitted the corrected year/major/campus.
    if any(_scopes(query).values()) and not (
        _document_numbers(query) is not None or _generic_document_reference(query)
        or _names_source(query, sources)
    ):
        return None
    if ambiguous or len(sources) != 1:
        return None
    return _typed_source_reference(sources[0])


def _names_source(query: str, sources: list[dict]) -> bool:
    if not _FOLLOW_UP_MARKERS.search(query) or re.search(r"말고|아니라|아니고|기준|^(?:아니|정정)", query):
        return False
    compact = re.sub(r"\s+", "", query)
    return any(
        (title := re.sub(r"\s+", "", str(cue.get("title", "")))) and title in compact
        for cue in sources
    )


def requires_source_identity(query: str, history_text: str) -> bool:
    """A requested document cannot be replaced by a title lookalike.

    Keep this separate from resolution: older title-only cues can explain the
    user's topic, but cannot authorize an answer about a particular document.
    """
    if _document_numbers(query) is not None or _generic_document_reference(query):
        return True
    if not needs_context_rewrite(query, history_text):
        return False
    _, sources, _ = _resolved_context(query, history_text)
    if _names_source(query, sources):
        return True
    if any(_scopes(query).values()):
        return False
    return len(sources) == 1 and (
        _typed_source_reference(sources[0]) is not None
        or has_lexical_overlap(query, str(sources[0].get("title", "")))
    )


def relevant_history(query: str, history_text: str) -> str:
    """Expose effective user constraints and source cues, never old answer facts."""
    if not history_allows_context_rewrite(query, history_text):
        return ""
    questions, sources, ambiguous = _resolved_context(query, history_text)
    if ambiguous:
        return ""
    return "\n".join([f"사용자: {question}" for question in questions] + [
        "이전 출처(검색 단서): " + json.dumps(cue, ensure_ascii=False) for cue in sources
    ])


def conversation_recall(query: str, history_text: str) -> SmalltalkReply | None:
    if re.fullmatch(r"(?:방금|아까|직전에?)\s*(?:내가\s*)?(?:뭐|무엇을)\s*(?:물어봤지|물었지|질문했지)[?.!\s]*", query.strip()):
        previous = extract_last_user_question(history_text)
        return SmalltalkReply(
            f'직전에 "{previous}"라고 물어보셨어요.' if previous else "이 채팅에서 확인할 수 있는 이전 질문이 없어요.",
            "conversation_recall",
        )
    return None


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
    # Short standalone questions do not inherit a topic. Explicit references
    # (including noun-free elaboration) are a separate continuation signal.
    if not history_allows_context_rewrite(text, history_text):
        return False
    if _FOLLOW_UP_MARKERS.search(text) or _explicit_reference(text):
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
    if not history_allows_context_rewrite(query, history_text):
        return query
    questions, sources, ambiguous = _resolved_context(query, history_text)
    if ambiguous:
        return query
    parts = list(dict.fromkeys([query.strip(), *questions, *(str(cue.get("title", "")) for cue in sources)]))
    reference = followup_source_reference(query, history_text)
    if reference is not None:
        parts.append(reference.domain_hint)
    return " ".join(part for part in parts if part).strip()


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
    "relevant_history",
    "conversation_recall",
    "followup_source_clarification",
    "followup_source_reference",
    "FollowupSourceReference",
    "requires_source_identity",
    "effective_scope_query",
]
