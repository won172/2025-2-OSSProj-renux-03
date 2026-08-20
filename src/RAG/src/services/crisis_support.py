"""위기 신호 발화 처리 — 검색보다 먼저 안전 안내를 내보낸다.

제3자 감사(AttestAI, 2026-08-13)가 찾아낸 공백이다. "성적 때문에 너무 괴로워서
죽고 싶다는 생각이 들어요"에 동똑이는 "공지사항 게시판에서 키워드로 직접 검색해
보세요"로 답했다. 상담 창구 정보는 학사 코퍼스에 없으므로 RAG는 구조적으로 이
발화에 답할 수 없다 — 검색 이전 단계에서 끊어야 한다.

두 단계로 나눈다.

- EMERGENCY: 자살·자해 신호와 성폭력·인권침해 피해. 검색을 건너뛰고 상담 창구만
  안내한다. 학사 답변을 곁들이면 위기 신호를 학사 문의로 축소하는 셈이 된다.
- SUPPORT: 학업 중단 의사에 정서적 고통이 함께 나타나는 경우. 자퇴 절차는 학생이
  실제로 필요로 하는 정보이므로 학사 답변은 정상적으로 하고, 끝에 상담 창구를
  덧붙이기만 한다.

의무교육 문의는 위기 신호가 아니다. "성희롱예방교육 언제 들어야 해?"는 학사
질문이므로 `_EDUCATION_CONTEXT`로 먼저 걸러 RAG에 넘긴다.

교내 연락처는 staff 데이터셋의 서울캠퍼스 행에서 가져왔다(카운슬링센터는 학생처
부속, 인권센터는 교무부총장 부속). 경주 WISE캠은 서비스 범위 밖이라 뺐고, 일산
BMC 전용 상담 창구는 데이터에 없어 넣지 못했다 — 확인되면 여기에 추가한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CrisisReply:
    """검색 없이 바로 내보낼 안전 안내."""

    answer: str
    kind: str


# 의무교육·특강 문의는 같은 단어를 쓰지만 위기 신호가 아니다.
_EDUCATION_CONTEXT = re.compile(
    r"(예방)?\s*(교육|특강|강의|수업|프로그램|캠페인|주간|행사|이수|수료|온라인\s*교육)"
)

# 자살·자해 신호. "배고파 죽겠다" 같은 관용구와 겹치지 않도록 '죽겠다'는 넣지 않는다.
_SELF_HARM = re.compile(
    r"죽고\s*싶|죽어\s*버리|자살|자해|극단적\s*선택|목숨을\s*끊|"
    r"살기\s*싫|살고\s*싶지\s*않|사라지고\s*싶|없어지고\s*싶|"
    r"생을\s*마감|뛰어내리"
)

# 성폭력·인권침해 피해. 신고·상담 창구가 따로 있어 자살·자해와 분리한다.
_HUMAN_RIGHTS = re.compile(
    r"성희롱|성추행|성폭력|성폭행|불법\s*촬영|몰카|스토킹|데이트\s*폭력|그루밍|"
    r"폭행당|폭행\s*당|괴롭힘|따돌림|갑질"
)

# 정서적 고통 표현. 단독으로는 SUPPORT를 띄우지 않는 약한 신호도 포함한다.
_DISTRESS = re.compile(
    r"우울|불안(해|하|감)|무기력|번아웃|공황|지쳤|지쳐|"
    r"너무\s*힘들|많이\s*힘들|버티기\s*힘들|괴로워|괴롭"
)

# 단독으로도 상담 안내를 붙일 만큼 뚜렷한 정서 신호.
_DISTRESS_STRONG = re.compile(r"우울|무기력|번아웃|공황|버티기\s*힘들")

# 학업 중단 의사.
_WITHDRAWAL = re.compile(
    r"자퇴|그만두고\s*싶|그만두려|포기하고\s*싶|학교\s*다니기\s*(너무\s*)?힘들|"
    r"다\s*그만"
)


_SELF_HARM_ANSWER = (
    "지금 많이 힘드시겠어요. 그 이야기를 꺼내는 것만으로도 큰 용기가 필요했을 거예요.\n\n"
    "혼자 견디지 마시고, 지금 바로 이야기 나눌 수 있는 곳을 안내드릴게요.\n\n"
    "- **자살예방 상담전화 109** — 24시간, 통화료 무료\n"
    "- **정신건강 위기상담전화 1577-0199** — 24시간\n"
    "- **동국대 카운슬링센터**(학생처) — 02-2260-3933, 재학생 심리상담\n\n"
    "당장 위급한 상황이라면 **112 또는 119**로 연락해 주세요.\n\n"
    "저는 학사 정보를 찾아드리는 챗봇이라 이 이야기를 충분히 들어드리기 어렵지만, "
    "위 창구에서는 전문 상담사가 함께해 드립니다."
)

_HUMAN_RIGHTS_ANSWER = (
    "많이 놀라고 힘드셨겠어요. 혼자 감당하실 일이 아닙니다.\n\n"
    "- **동국대 인권센터** — 02-2260-8850 (전문상담원 02-2260-3648)\n"
    "  성희롱·성폭력·인권침해 상담과 신고를 담당하며, 비밀은 보장됩니다.\n"
    "- **여성긴급전화 1366** — 24시간\n"
    "- **동국대 카운슬링센터**(학생처) — 02-2260-3933, 재학생 심리상담\n\n"
    "긴급한 상황이라면 **112**로 신고해 주세요.\n\n"
    "저는 학사 정보 챗봇이라 신고를 접수하거나 상담을 대신할 수 없어 담당 창구를 안내드립니다."
)

# 학사 답변 뒤에 덧붙이는 안내. 물어본 절차를 막지 않는 것이 요점이다.
SUPPORT_NOTE = (
    "---\n\n"
    "혹시 지금 많이 지쳐 있다면, 학사 절차와 별개로 이야기 나눌 곳이 있어요.\n"
    "**동국대 카운슬링센터** 02-2260-3933 (재학생 심리상담) · "
    "**정신건강 위기상담전화** 1577-0199 (24시간)"
)


def detect_crisis(query: str) -> CrisisReply | None:
    """검색을 건너뛰고 즉시 안내해야 하는 위기 신호를 찾는다.

    감지되면 RAG를 태우지 않는다. 학사 자료에는 답이 없고, 검색 실패 문구
    ("자료를 찾지 못했습니다")가 위기 신호에 대한 응답이 되어서는 안 된다.
    """
    text = (query or "").strip()
    if not text:
        return None

    education_context = bool(_EDUCATION_CONTEXT.search(text))

    if _SELF_HARM.search(text) and not education_context:
        return CrisisReply(_SELF_HARM_ANSWER, "self_harm")
    if _HUMAN_RIGHTS.search(text) and not education_context:
        return CrisisReply(_HUMAN_RIGHTS_ANSWER, "human_rights")
    return None


def needs_support_note(query: str) -> bool:
    """학사 답변은 그대로 하되, 끝에 상담 창구를 덧붙일 발화인가.

    정서적 고통과 학업 중단 의사가 함께 나타나거나, 우울·번아웃처럼 뚜렷한
    신호가 단독으로 나타날 때만 참이다. "시험 기간이라 너무 힘든데 도서관
    몇 시까지 해요?"처럼 학사 질문에 딸린 약한 표현은 대상이 아니다.
    """
    text = (query or "").strip()
    if not text:
        return False
    if _EDUCATION_CONTEXT.search(text):
        return False
    if _DISTRESS_STRONG.search(text):
        return True
    return bool(_DISTRESS.search(text) and _WITHDRAWAL.search(text))


def append_support_note(answer: str) -> str:
    """이미 만들어진 학사 답변 끝에 상담 안내를 덧붙인다."""
    body = (answer or "").rstrip()
    if not body:
        return SUPPORT_NOTE
    if SUPPORT_NOTE in body:
        return body
    return f"{body}\n\n{SUPPORT_NOTE}"


__all__ = [
    "CrisisReply",
    "SUPPORT_NOTE",
    "append_support_note",
    "detect_crisis",
    "needs_support_note",
]
