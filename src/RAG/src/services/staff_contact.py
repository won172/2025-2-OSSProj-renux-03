"""연락처 질의에서 어떤 교직원 행을 먼저 보여줄지 정한다.

교직원 명부는 사람 단위다. 학과사무실이라는 행이 따로 있는 게 아니라, 그 학과에 속한
조교·팀원 같은 행정직 행이 사실상 사무실 연락처다. 그런데 직위가 구조화돼 있지 않아
순위에 반영되지 않았고, 실측에서 이런 답이 나왔다.

    "컴퓨터공학과 학과사무실 전화번호"  → 1위 대학원학과주임교수(교수)
    "호텔관광외식경영학부 사무실 연락처"  → 1위 조교수, 전화번호 없음

두 가지를 고친다.

1. 번호를 물었는데 번호가 없는 행을 위로 올리지 않는다.
2. '사무실·행정실'을 물으면 행정직 행을 교수 행보다 위로 올린다.

또 하나 중요한 것: 학과 192개 중 43개(22%)는 행정직 행이 아예 없다(통계학과 포함).
그럴 때 교수 번호를 사무실 번호인 양 주면 안 되므로, 무엇을 주는지 밝히도록
`describe_contact_fallback()`이 안내 문구를 만든다.
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

# 질의가 '사람'이 아니라 '부서 창구'를 찾고 있다는 신호.
OFFICE_INTENT = re.compile(r"사무실|행정실|교학팀|과사무실|과사|학과\s*사무|행정|조교")
# 연락 수단을 묻는 신호. 이때는 번호 없는 행이 위로 오면 안 된다.
CONTACT_INTENT = re.compile(r"전화|연락처|번호|내선|이메일|메일|문의")

# 교원 표기. 행정직 판정보다 먼저 본다.
# '학과장'·'학부장'은 교원이 맡는 보직이라 여기 둔다 — 명부의 '학과장(B)'가 아래
# '과장'에 부분일치해 행정 창구로 잡히던 오탐을 테스트가 잡았다.
_FACULTY = ("교수", "강사", "연구원", "연구소장", "센터장", "학과장", "학부장", "원장", "총장")
# 학과 창구 역할을 하는 직위. 부분일치 오탐을 막으려 경계를 함께 본다.
# '조교'는 '조교수'의 일부가 아닐 때만, '과장'은 '학과장'의 일부가 아닐 때만 인정한다.
_ADMINISTRATIVE = re.compile(
    r"조교(?!수)|팀원|팀장|(?<!학)과장|주임(?!교수)|직원|실장|부장|파트장"
)


def is_administrative(position: str) -> bool:
    """행정 창구 직위인가. '조교수'·'학과장'은 교원이므로 제외한다."""
    text = str(position or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _FACULTY):
        return False
    return bool(_ADMINISTRATIVE.search(text))


def has_phone(phone: str) -> bool:
    return bool(str(phone or "").strip())


def contact_sort_key(
    position: str,
    phone: str,
    *,
    office_intent: bool,
) -> tuple[int, int]:
    """작을수록 먼저. (번호 유무, 직위 적합도) 순으로 본다.

    번호 유무를 앞에 두는 이유는 단순하다 — 번호를 물었는데 번호가 없는 행은
    직위가 아무리 맞아도 답이 될 수 없다.
    """
    phone_rank = 0 if has_phone(phone) else 1
    if not office_intent:
        return (phone_rank, 0)
    return (phone_rank, 0 if is_administrative(position) else 1)


def describe_contact_fallback(
    department: str,
    positions: Sequence[str],
    *,
    office_intent: bool,
) -> str | None:
    """사무실 행이 없어 교원 연락처만 줄 때 붙일 안내(없으면 None).

    43개 학과에는 행정직 행이 없다. 그런 학과에서 교수 번호를 사무실 번호처럼
    제시하면 사용자는 학과사무실인 줄 알고 전화한다.
    """
    if not office_intent:
        return None
    if any(is_administrative(position) for position in positions):
        return None
    label = f"{department} " if department else ""
    return (
        f"{label}학과사무실로 등록된 연락처는 보유 자료에서 확인되지 않습니다. "
        "아래는 해당 학과 교원 연락처입니다."
    )


def office_intent_in(query: str) -> bool:
    return bool(OFFICE_INTENT.search(str(query or "")))


def contact_intent_in(query: str) -> bool:
    return bool(CONTACT_INTENT.search(str(query or "")))


__all__ = [
    "contact_intent_in",
    "contact_sort_key",
    "describe_contact_fallback",
    "has_phone",
    "is_administrative",
    "office_intent_in",
]
