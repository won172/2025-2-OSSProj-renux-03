"""검색 실패 시 정형 데이터로 답을 만드는 구제 경로 테스트.

폴백 로그에서 실제로 실패했던 질문 문구를 그대로 입력으로 쓴다.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.direct_answer import (  # noqa: E402
    MealRow,
    NoticePeriodRow,
    ScheduleRow,
    answer_future_notice_period,
    answer_future_unannounced,
    answer_meal,
    answer_schedule_when,
    extract_restaurant_hint,
    extract_schedule_event_terms,
    future_publication_years,
    is_meal_question,
    is_meal_direct_question,
    is_schedule_direct_question,
    is_schedule_when_question,
    resolve_relative_window,
)
from src.utils.briefing import split_meal_corners  # noqa: E402

# 2026-07-30은 목요일 → 이번 주 = 07-27(월) ~ 08-02(일)
TODAY = date(2026, 7, 30)


def test_future_publication_without_matching_official_material_is_an_explicit_answer():
    result = answer_future_unannounced(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        ["2026학년도 1학기 교환학생 모집은 2025년 9월에 진행"],
        TODAY,
    )

    assert result is not None
    assert result.kind == "future_unannounced"
    assert "아직 공고가 확인되지 않았습니다" in result.answer
    assert result.sources == []


def test_future_publication_guard_yields_to_matching_future_material():
    result = answer_future_unannounced(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        ["2027학년도 1학기 교환학생 지원 기간은 2026.09.01~2026.09.10"],
        TODAY,
    )

    assert result is None


def test_future_notice_without_the_requested_date_is_not_treated_as_the_fact():
    result = answer_future_unannounced(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        ["2027학년도 1학기 교환학생 선발 일정 예정, 외부 페이지에서 추후 안내"],
        TODAY,
    )

    assert result is not None
    assert "관련 안내는 확인되지만" in result.answer


def test_future_notice_image_transcript_with_concrete_period_yields_to_rag():
    result = answer_future_unannounced(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        [
            "2027-1학기 파견 영어권 교환학생 선발 일정\n"
            "[본문 이미지 전사] 지원서 접수: 2026. 8. 5.(수) ~ 8. 13.(목) 14시"
        ],
        TODAY,
    )

    assert result is None


def test_future_notice_labeled_application_period_is_answered_deterministically():
    result = answer_future_notice_period(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        [
            NoticePeriodRow(
                notice_id=6024,
                title="2027-1학기 파견 영어권 교환학생 선발 일정",
                published_at="2026-07-02",
                url="https://www.dongguk.edu/article/INTEXNOTICE/detail/26765300",
                content=(
                    "선발대학 & 지원방법 공지: 2026. 8. 5.(수) 17시\n"
                    "지원서 접수: 2026. 8. 5.(수) ~ 8. 13.(목) 14시, nDRIMS 접수\n"
                    "합격자 발표: 2026. 8. 27.(목) 17시 이후\n"
                    "본 일정은 예상 일정이며 일부 변경될 수 있습니다."
                ),
            )
        ],
        TODAY,
    )

    assert result is not None
    assert result.kind == "future_notice_period"
    assert "교환학생" in result.answer
    assert "지원서 접수" in result.answer
    assert "2026-08-05" in result.answer
    assert "2026-08-13 14시" in result.answer
    assert "변경될 수 있습니다" in result.answer
    assert "2026-08-27" not in result.answer
    assert result.sources[0]["metadata"]["apply_deadline"] == "2026-08-13"


def test_future_notice_period_does_not_guess_from_unlabeled_dates():
    result = answer_future_notice_period(
        "2027학년도 1학기 교환학생 지원 기간 알려줘",
        [
            NoticePeriodRow(
                title="2027-1학기 교환학생 안내",
                content="설명회 2026. 8. 5. ~ 8. 6. / 합격자 발표 2026. 8. 27.",
            )
        ],
        TODAY,
    )

    assert result is None


@pytest.mark.parametrize(
    "query",
    [
        "2026학년도 교환학생 지원 기간 알려줘",
        "2027학년도 규정 알려줘",
        "내년에는 학교가 어떻게 바뀔까?",
    ],
)
def test_future_publication_guard_does_not_claim_unannounced_outside_its_scope(query):
    assert future_publication_years(query, TODAY) == ()


# ---------------------------------------------------------------- 상대 날짜

@pytest.mark.parametrize("query,expected_label,expected_start,expected_end", [
    ("오늘 학식 뭐 나와?", "오늘", date(2026, 7, 30), date(2026, 7, 30)),
    ("내일은 무슨날이아", "내일", date(2026, 7, 31), date(2026, 7, 31)),
    ("어제 올라온 공지 있어?", "어제", date(2026, 7, 29), date(2026, 7, 29)),
    ("이번 주 학사일정 알려줘", "이번 주", date(2026, 7, 27), date(2026, 8, 2)),
    ("다음 주에 진행되는 행사 있어?", "다음 주", date(2026, 8, 3), date(2026, 8, 9)),
    ("이번 달 학사일정 알려줘", "이번 달", date(2026, 7, 1), date(2026, 7, 31)),
])
def test_상대_날짜_표현을_실제_기간으로_바꾼다(query, expected_label, expected_start, expected_end):
    window = resolve_relative_window(query, TODAY)
    assert window is not None
    assert (window.label, window.start, window.end) == (expected_label, expected_start, expected_end)


def test_이번_주는_주중에_물어도_월요일부터_잡는다():
    """주 중간(목)에 물어도 같은 주 지난 요일 일정까지 보여야 '이번 주 일정'이다."""
    window = resolve_relative_window("이번 주 일정", TODAY)
    assert window.start.weekday() == 0  # 월요일
    assert window.start < TODAY


def test_상대_날짜가_없는_질문은_기간을_만들지_않는다():
    assert resolve_relative_window("졸업요건 알려줘", TODAY) is None


@pytest.mark.parametrize("query", [
    "방학 시작일이 언제야?",   # 시'작일' → '작일'(어제)로 오탐하던 실제 버그
    "개강일이 언제야?",
    "설명일정 알려줘",         # 설'명일' → '명일'(내일) 오탐 방지
    "지금 일정 뭐 있어",       # '금일' 오탐 방지(공백 제거 후 '지금일정')
])
def test_다른_단어에_섞인_한자어_축약형을_상대_날짜로_오해하지_않는다(query):
    assert resolve_relative_window(query, TODAY) is None


@pytest.mark.parametrize("word,offset", [("금일", 0), ("명일", 1), ("작일", -1)])
def test_한자어_축약형이_단독으로_쓰이면_인정한다(word, offset):
    from datetime import timedelta
    window = resolve_relative_window(f"{word} 학사일정 알려줘", TODAY)
    assert window is not None
    assert window.start == TODAY + timedelta(days=offset)


def test_월_경계를_넘는_이번달_계산이_정확하다():
    """31일 더하기 방식은 월말에 다음 달을 건너뛸 수 있어 월 단위로 계산한다."""
    window = resolve_relative_window("이번 달 학사일정", date(2026, 1, 31))
    assert (window.start, window.end) == (date(2026, 1, 1), date(2026, 1, 31))

    window = resolve_relative_window("다음 달 학사일정", date(2026, 1, 31))
    assert (window.start, window.end) == (date(2026, 2, 1), date(2026, 2, 28))

    window = resolve_relative_window("다음 달 학사일정", date(2026, 12, 15))
    assert (window.start, window.end) == (date(2027, 1, 1), date(2027, 1, 31))


# ---------------------------------------------------------------- 학사일정 판정

@pytest.mark.parametrize("query", [
    "2026학년도 2학기 개강일이 언제야?",
    "이번 학기 종강일이 언제야?",
    "시험 기간이 언제야?",
    "2학기 수강신청 장바구니 기간은 언제야?",
    "방학 시작일이 언제야?",
    "성적 정정은 언제까지 가능한가요?",
])
def test_시점_질문으로_인식한다(query):
    assert is_schedule_when_question(query) is True


@pytest.mark.parametrize("query", [
    "안녕",
    "졸업요건이 뭐야",          # 날짜 신호 없음
    "수강신청 취소는 어떻게 해?",  # '어떻게' = 절차 질문
])
def test_시점_질문이_아닌_것은_걸러낸다(query):
    assert is_schedule_when_question(query) is False


def test_사건_키워드를_표_검색어로_바꾼다():
    assert extract_schedule_event_terms("개강일 언제야") == ("개강",)
    assert extract_schedule_event_terms("장바구니 기간") == ("희망강의", "장바구니")
    # 더 구체적인 규칙이 먼저 매칭된다(중간고사 → '시험'이 아니라 '중간고사').
    assert extract_schedule_event_terms("중간고사 언제") == ("중간고사", "중간시험")
    assert extract_schedule_event_terms("커피 마실래") == ()


def test_구체적인_표현이_일반_표현보다_먼저_매칭된다():
    """'수강신청 장바구니'를 일반 수강신청으로 잡으면 엉뚱한 일정을 답한다(실제 버그)."""
    assert extract_schedule_event_terms("2학기 수강신청 장바구니 기간은 언제야?") == ("희망강의", "장바구니")
    assert extract_schedule_event_terms("수강 정정 기간") == ("정정",)
    assert extract_schedule_event_terms("기말시험 일정") == ("기말고사", "기말시험")


def test_데이터에_없는_일정은_구제하지_않고_기존_경로에_맡긴다():
    """장바구니 일정이 표에 없으면 비슷한 다른 일정으로 얼버무리지 않아야 한다."""
    only_registration = [ScheduleRow("2026학년도 2학기 대학원 수강신청", date(2026, 7, 31), date(2026, 8, 5))]
    assert answer_schedule_when("2학기 수강신청 장바구니 기간은 언제야?", only_registration, TODAY) is None


def test_일반_시험_질문은_중간_기말시험을_먼저_보여준다():
    """'시험 기간'을 물으면 대학원 종합시험 접수보다 학부 중간·기말시험이 먼저다."""
    rows = [
        ScheduleRow("종합시험 접수", date(2026, 7, 20), date(2026, 7, 31)),
        ScheduleRow("2학기 중간시험", date(2026, 10, 20), date(2026, 10, 27)),
    ]
    result = answer_schedule_when("시험 기간이 언제야?", rows, TODAY)
    assert result is not None
    assert result.answer.startswith("2학기 중간시험")


def test_띄어쓰기와_학부_대학원_대상을_구분한다():
    rows = [
        ScheduleRow(
            "2026학년도 2학기 대학원 수강 신청",
            date(2026, 7, 31),
            date(2026, 8, 5),
            row_id=1,
        ),
        ScheduleRow(
            "2026학년도 2학기 학부 수강 신청",
            date(2026, 8, 3),
            date(2026, 8, 7),
            row_id=2,
        ),
    ]

    generic = answer_schedule_when("수강신청 언제야?", rows, TODAY)
    undergraduate = answer_schedule_when("학부 수강신청 언제야?", rows, TODAY)
    graduate = answer_schedule_when("대학원 수강신청 언제야?", rows, TODAY)

    assert "학부 수강 신청" in generic.answer
    assert "2026년 8월 3일" in generic.answer
    assert "학부 수강 신청" in undergraduate.answer
    assert "대학원 수강 신청" in graduate.answer
    assert "2026년 7월 31일" in graduate.answer
    assert generic.sources[0]["metadata"]["audience"] == "undergraduate"
    assert generic.sources[0]["chunk_id"] == "schedule:2"


def test_시점형_일정만_검색보다_먼저_조회한다():
    assert is_schedule_direct_question("수강신청 언제야?", TODAY) is True
    assert is_schedule_direct_question("이번 주 학사일정 알려줘", TODAY) is True
    assert is_schedule_direct_question("수강신청 방법 알려줘", TODAY) is False


# ---------------------------------------------------------------- 학사일정 답변

SCHEDULE = [
    ScheduleRow("2026학년도 2학기 개강", date(2026, 9, 1), date(2026, 9, 1)),
    ScheduleRow("여름 계절학기", date(2026, 6, 22), date(2026, 7, 10)),
    ScheduleRow("2학기 희망강의(장바구니) 신청", date(2026, 7, 28), date(2026, 7, 30)),
    ScheduleRow("하계 방학", date(2026, 6, 20), date(2026, 8, 31)),
    ScheduleRow("2학기 수강신청", date(2026, 8, 10), date(2026, 8, 14)),
]


def test_사건_질문은_오늘_이후_가장_가까운_일정을_날짜로_답한다():
    result = answer_schedule_when("2026학년도 2학기 개강일이 언제야?", SCHEDULE, TODAY)
    assert result is not None
    assert "2026년 9월 1일(화)" in result.answer
    assert result.kind == "schedule_event"
    assert result.sources and result.sources[0]["source"] == "schedule"


def test_기간_질문은_그_기간과_겹치는_일정을_모아_답한다():
    result = answer_schedule_when("이번 주 학사일정 알려줘", SCHEDULE, TODAY)
    assert result is not None
    assert result.kind == "schedule_window"
    # 07-27~08-02와 겹치는 것: 장바구니(7/28~7/30), 방학(6/20~8/31)
    assert "장바구니" in result.answer
    assert "방학" in result.answer
    # 겹치지 않는 개강(9/1)은 빠져야 한다.
    assert "개강" not in result.answer


def test_기간에_해당하는_일정이_없으면_가장_가까운_일정을_알려준다():
    """'없습니다'로 끝내면 사용자는 다음 행동을 알 수 없다."""
    sparse = [ScheduleRow("2학기 수강신청", date(2026, 8, 10), date(2026, 8, 14))]
    result = answer_schedule_when("이번 주 학사일정 알려줘", sparse, TODAY)
    assert result is not None
    assert result.kind == "schedule_nearest"
    assert "해당하는 학사일정은 없습니다" in result.answer
    assert "가장 가까운 일정은" in result.answer
    assert "2026년 8월 10일" in result.answer


def test_모두_지난_일정이면_마지막_기록을_밝힌다():
    past = [ScheduleRow("1학기 종강", date(2026, 6, 19), date(2026, 6, 19))]
    result = answer_schedule_when("종강일이 언제야?", past, TODAY)
    assert result is not None
    assert result.kind == "schedule_past"
    assert "가장 최근 기록은" in result.answer


def test_일치하는_일정이_없으면_None을_돌려_기존_폴백에_맡긴다():
    result = answer_schedule_when("졸업식 언제야?", SCHEDULE, TODAY)
    assert result is None


def test_하루짜리_일정은_날짜를_한_번만_쓴다():
    result = answer_schedule_when("개강일 언제야?", SCHEDULE, TODAY)
    assert result.answer.count("2026년 9월 1일") == 1


# ---------------------------------------------------------------- 학식

MEAL_TEXT = (
    "[중식 A코너 6,500원] 쌀밥 제육볶음 미소국/배추김치 / "
    "[중식 B코너 7,500원] 중국식볶음밥 돼지고기땅콩강정 / "
    "[석식 6,500원] 쌀밥 고추마요치킨커틀렛"
)

MEALS = [
    MealRow(date(2026, 7, 30), "상록원 2층", MEAL_TEXT),
    MealRow(date(2026, 7, 30), "누리터식당(일산캠퍼스)", "휴무", is_closed=True),
    MealRow(date(2026, 7, 31), "상록원 2층", "[중식] 김치찌개"),
]


@pytest.mark.parametrize("query", ["오늘 학식 뭐 나와?", "오늘 학식 뭐야", "솥앤누들 가격", "이번주 메뉴 알려줘"])
def test_학식_질문으로_인식한다(query):
    assert is_meal_question(query) is True


def test_학식_질문이_아닌_것은_걸러낸다():
    assert is_meal_question("졸업요건 알려줘") is False


def test_식단_표로_답할_수_없는_학식_질문은_직접_조회하지_않는다():
    assert is_meal_direct_question("오늘 학식 뭐 나와?") is True
    assert is_meal_direct_question("솥앤누들 가격") is True
    assert is_meal_direct_question("학식 결제수단 알려줘") is False


def test_오늘_학식은_표에서_직접_찾아_코너별로_답한다():
    """실제 폴백 사례: meals에 그날 식단이 있는데도 벡터 검색이 매칭하지 못했다."""
    result = answer_meal("오늘 학식 뭐 나와?", MEALS, TODAY, split_meal_corners)
    assert result is not None
    assert "2026년 7월 30일(목)" in result.answer
    assert "상록원 2층" in result.answer
    assert "제육볶음" in result.answer
    # 슬래시로 이어진 반찬도 유지된다.
    assert "배추김치" in result.answer
    # 휴무 식당은 목록에서 빠진다.
    assert "누리터" not in result.answer
    assert result.sources and result.sources[0]["source"] == "meals"


def test_내일_학식은_다음_날_행을_찾는다():
    result = answer_meal("내일 학식 뭐 나와?", MEALS, TODAY, split_meal_corners)
    assert "2026년 7월 31일(금)" in result.answer
    assert "김치찌개" in result.answer


def test_가격_질문이면_가격_표기_위치를_함께_안내한다():
    result = answer_meal("오늘 학식 가격 얼마야?", MEALS, TODAY, split_meal_corners)
    assert "가격은 코너 이름에 함께 표기" in result.answer
    assert "6,500원" in result.answer


def test_특정_식당을_물으면_그_식당만_보여준다():
    rows = MEALS + [MealRow(date(2026, 7, 30), "솥앤누들", "[일반] 잔치국수 5,000원")]
    result = answer_meal("솥앤누들 가격", rows, TODAY, split_meal_corners)
    assert "솥앤누들" in result.answer
    assert "상록원" not in result.answer


def test_식당_별칭을_인식한다():
    assert extract_restaurant_hint("경영관 D-Flex 메뉴") != ()
    assert extract_restaurant_hint("디플렉스 오늘 뭐 나와") != ()
    assert extract_restaurant_hint("아무 식당") == ()


def test_그날_모든_식당이_휴무면_휴무라고_알려준다():
    closed = [MealRow(date(2026, 7, 30), "상록원 2층", "휴무", is_closed=True)]
    result = answer_meal("오늘 학식 뭐 나와?", closed, TODAY, split_meal_corners)
    assert result is not None
    assert result.kind == "meal_closed"
    assert "운영하지 않습니다" in result.answer


def test_휴무일에는_다음_운영일을_함께_알려준다():
    """방학 중에는 며칠씩 연달아 휴무라 '언제 여는지'가 실제 궁금증이다."""
    rows = [
        MealRow(date(2026, 7, 30), "상록원 2층", "휴무", is_closed=True),
        MealRow(date(2026, 7, 31), "상록원 2층", "휴무", is_closed=True),
        MealRow(date(2026, 8, 3), "상록원 2층", "[중식] 제육덮밥"),
    ]
    result = answer_meal("오늘 학식 뭐 나와?", rows, TODAY, split_meal_corners)
    assert result.kind == "meal_closed"
    assert "다음 운영일은 2026년 8월 3일(월)" in result.answer
    assert "상록원 2층" in result.answer


def test_이후_운영일_정보가_없으면_휴무만_알린다():
    closed_only = [MealRow(date(2026, 7, 30), "상록원 2층", "휴무", is_closed=True)]
    result = answer_meal("오늘 학식 뭐 나와?", closed_only, TODAY, split_meal_corners)
    assert "다음 운영일" not in result.answer


def test_해당_날짜_데이터가_없으면_None을_돌려_기존_폴백에_맡긴다():
    result = answer_meal("오늘 학식 뭐 나와?", [], TODAY, split_meal_corners)
    assert result is None


def test_수강신청_정정은_수강신청이_아니라_정정_일정이다():
    """'수강신청 정정'은 정정 기간을 묻는 말이다.

    로그의 `이번 학기 수강정정 기간을 날짜별로 알려줘`가 근거검증에 실패했다.
    `수강\\s*정정`만으로는 "수강신청 정정"처럼 사이에 '신청'이 낀 표현을 놓치고,
    아래 수강신청 규칙에 먼저 걸려 8월 수강신청 날짜를 답한다.
    """
    rows = [
        ScheduleRow("2026학년도 2학기 학부 수강 신청", date(2026, 8, 3), date(2026, 8, 7)),
        ScheduleRow("수강신청 확인 및 정정", date(2026, 9, 1), date(2026, 9, 7)),
    ]
    for 질문 in ("수강정정 기간", "수강신청 정정 언제야", "이번 학기 수강 신청 정정 기간"):
        answer = answer_schedule_when(질문, rows, date(2026, 8, 4))
        assert answer is not None, 질문
        assert "9월 1일" in answer.answer, 질문
        assert "8월 3일" not in answer.answer, 질문


def test_정정을_묻지_않으면_수강신청_일정을_그대로_답한다():
    rows = [
        ScheduleRow("2026학년도 2학기 학부 수강 신청", date(2026, 8, 3), date(2026, 8, 7)),
        ScheduleRow("수강신청 확인 및 정정", date(2026, 9, 1), date(2026, 9, 7)),
    ]
    answer = answer_schedule_when("수강신청 언제야?", rows, date(2026, 8, 4))
    assert answer is not None and "8월 3일" in answer.answer
