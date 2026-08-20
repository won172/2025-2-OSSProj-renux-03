from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.services.langchain_chat import (  # noqa: E402
    build_followup_question_details,
    source_bounded_followup_fallback,
    validate_followup_questions,
)
from src.services.source_contract import normalized_source_contract, source_reference  # noqa: E402
from api import rag_service  # noqa: E402


SOURCES = [
    {
        "source": "notices",
        "title": "2026학년도 교내장학 신청 안내",
        "snippet": "교내장학 신청 서류와 신청 결과 확인 방법은 학생 포털에서 안내합니다.",
        "metadata": {"campus_scope": "seoul"},
    }
]


def test_followups_remove_repeats_duplicates_wrong_campus_and_inventions():
    candidates = [
        "교내장학 신청 방법이 궁금해요",  # original repeat
        "장학 신청 서류는 무엇인가요?",  # valid
        "장학 신청 서류가 무엇인가요?",  # near duplicate
        "WISE캠퍼스 장학도 신청할 수 있나요?",  # wrong campus
        "와이즈 캠퍼스 장학 신청 서류도 같은가요?",  # wrong campus alias
        "신청 금액은 500만원인가요?",  # invented amount
        "신청 마감은 12월 31일인가요?",  # invented date
        "내일 날씨는 어떤가요?",  # unsupported domain
        "오늘 학식 메뉴는 무엇인가요?",  # meals absent from supported domains
        "신청 결과는 어디에서 확인하나요?",  # valid
    ]
    result = validate_followup_questions(
        candidates,
        question="교내장학 신청 방법이 궁금해요",
        answer="교내장학은 안내된 신청 서류를 준비해 학생 포털에서 신청합니다.",
        source_context=SOURCES,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=5,
    )

    assert result == [
        "장학 신청 서류는 무엇인가요?",
        "신청 결과는 어디에서 확인하나요?",
    ]
    assert len(result) < 5  # 유효하지 않은 질문으로 정해진 개수를 채우지 않는다.


def test_followups_require_grounded_source_context():
    result = validate_followup_questions(
        ["신청 서류는 무엇인가요?"],
        question="신청 방법은?",
        answer="신청할 수 있습니다.",
        source_context=[],
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=3,
    )
    assert result == []


def test_followup_topic_normalization_preserves_two_syllable_nouns():
    sources = [{
        "source": "notices",
        "title": "AI소프트웨어융합학부 전공시험 일정 안내",
        "snippet": "학과 시험 일정과 추가 접수 절차를 안내합니다.",
        "metadata": {"campus_scope": "seoul"},
    }]
    result = validate_followup_questions(
        ["학과 시험 일정도 확인할까요?", "추가 접수 방법도 확인할까요?"],
        question="전공시험 일정 알려줘",
        answer="학과 시험 일정과 추가 접수는 공지에서 확인할 수 있어요.",
        source_context=sources,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=5,
    )

    assert result == ["학과 시험 일정도 확인할까요?", "추가 접수 방법도 확인할까요?"]


def test_distinctive_compound_can_ground_a_concise_followup_and_lineage():
    sources = [{
        "source": "notices",
        "title": "2026학년도 수강신청 장바구니 기간 안내",
        "snippet": "수강신청 장바구니 운영 기간을 안내합니다.",
        "chunk_id": "notice-cart-1",
        "metadata": {"campus_scope": "shared"},
    }]
    question = "장바구니에 담은 과목은 어디서 확인하나요?"

    suggestions = validate_followup_questions(
        [question],
        question="수강신청 장바구니 기간은 언제야?",
        answer="장바구니 기간은 공식 공지에서 확인할 수 있어요.",
        source_context=sources,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=5,
    )
    details = build_followup_question_details(suggestions, sources)

    assert suggestions == [question]
    assert details == [{"question": question, "source_refs": [source_reference(sources[0])]}]


def test_wise_followup_is_allowed_only_in_wise_scope_when_source_supports_it():
    wise_sources = [{
        "source": "rules",
        "title": "WISE캠퍼스 휴학 규정",
        "snippet": "WISE캠퍼스 휴학 신청 서류를 안내합니다.",
        "metadata": {"campus_scope": "wise"},
    }]
    result = validate_followup_questions(
        ["WISE캠퍼스 휴학 신청 서류는 무엇인가요?"],
        question="WISE캠퍼스 휴학 규정 알려줘",
        answer="WISE캠퍼스 휴학 신청은 규정에 따릅니다.",
        source_context=wise_sources,
        campus_scope="wise",
        supported_domains=["rules"],
        count=3,
    )
    assert result == ["WISE캠퍼스 휴학 신청 서류는 무엇인가요?"]


def test_answer_paths_do_not_wait_for_followup_generation():
    for endpoint in (rag_service.ask, rag_service.ask_stream):
        source = inspect.getsource(endpoint)
        assert "generate_followup_questions(" not in source

    followup_source = inspect.getsource(rag_service.followups)
    assert "context.eligible" in followup_source
    assert "generate_followup_questions(" in followup_source


def test_async_followup_timing_uses_same_millisecond_unit_as_total():
    timings = rag_service._with_followup_generation_timing(
        '{"total": 7905.0}',
        1.4418,
    )

    assert timings["total"] == 7905.0
    assert timings["followup_generation_async"] == 1441.8


@pytest.mark.asyncio
async def test_followup_endpoint_generates_only_for_grounded_logged_answer(monkeypatch):
    source = {
        **SOURCES[0],
        "chunk_id": "notice-async-1",
        "source_ref": source_reference({**SOURCES[0], "chunk_id": "notice-async-1"}),
    }
    context = rag_service.FollowupGenerationContext(
        question="교내장학 신청 방법은?",
        answer="교내장학 신청 서류를 준비하세요.",
        source_context=[source],
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        eligible=True,
    )
    generated = False

    async def fake_generate(*_args, **_kwargs):
        nonlocal generated
        generated = True
        return ["장학 신청 서류는 무엇인가요?"]

    monkeypatch.setattr(rag_service, "_load_followup_generation_context", lambda _request_id: context)
    monkeypatch.setattr(rag_service, "generate_followup_questions", fake_generate)
    monkeypatch.setattr(rag_service, "_merge_followup_observability_log", lambda *_args: None)

    response = await rag_service.followups(
        rag_service.FollowupRequest(requestId="async-followup-1")
    )

    assert generated is True
    assert response.questions == ["장학 신청 서류는 무엇인가요?"]
    assert response.question_details[0].source_refs == [source["source_ref"]]


@pytest.mark.asyncio
async def test_followup_endpoint_skips_ungrounded_answer(monkeypatch):
    context = rag_service.FollowupGenerationContext(
        question="질문",
        answer="답변",
        source_context=SOURCES,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        eligible=False,
    )

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("ungrounded answers must not generate followups")

    monkeypatch.setattr(rag_service, "_load_followup_generation_context", lambda _request_id: context)
    monkeypatch.setattr(rag_service, "generate_followup_questions", should_not_run)

    response = await rag_service.followups(
        rag_service.FollowupRequest(requestId="async-followup-2")
    )

    assert response.questions == []


def test_single_generic_token_overlap_does_not_pass_topic_validation():
    result = validate_followup_questions(
        [
            "장학 관련 기숙사 비용도 알려주세요",
            "장학 신청과 기숙사 비용의 관계는 무엇인가요?",
        ],
        question="교내장학 신청 방법은?",
        answer="교내장학 신청 서류를 준비하세요.",
        source_context=SOURCES,
        campus_scope="seoul_bmc",
        supported_domains=["notices"],
        count=3,
    )
    assert result == []


def test_grounding_disabled_or_unchecked_never_allows_followups():
    grounded = SimpleNamespace(checked=True, grounded=True)
    unchecked = SimpleNamespace(checked=False, grounded=True)
    rejected = SimpleNamespace(checked=True, grounded=False)

    assert rag_service._grounding_allows_followups(False, grounded) is False
    assert rag_service._grounding_allows_followups(True, unchecked) is False
    assert rag_service._grounding_allows_followups(True, rejected) is False
    assert rag_service._grounding_allows_followups(True, grounded) is True


def test_followup_details_bind_each_question_only_to_supporting_sources():
    unrelated = {
        "source": "staff",
        "title": "정보처 연락처",
        "snippet": "정보처 사무실 전화번호를 안내합니다.",
        "metadata": {"campus_scope": "seoul"},
    }
    details = build_followup_question_details(
        ["장학 신청 서류는 무엇인가요?", "기숙사 입사 비용은 얼마인가요?"],
        [*SOURCES, unrelated],
    )

    assert details == [{
        "question": "장학 신청 서류는 무엇인가요?",
        "source_refs": [source_reference(SOURCES[0])],
    }]


def test_followup_details_preserve_the_answer_transport_lineage():
    original = {
        "source": "schedule",
        "title": "2026학년도 2학기 학부 수강신청",
        "snippet": "학부 수강신청은 8월 3일부터 8월 7일까지 진행됩니다.",
        "chunk_id": "schedule-2026-2-registration",
        "metadata": {
            "campus_scope": "shared",
            "schedule_start": "2026-08-03",
            "schedule_end": "2026-08-07",
        },
    }
    transported_ref = source_reference(original)
    reduced_log_source = {
        **original,
        "metadata": {},
        "source_ref": transported_ref,
    }

    details = build_followup_question_details(
        ["학부 수강신청 기간도 확인할까요?"],
        [reduced_log_source],
    )

    assert transported_ref != source_reference(reduced_log_source)
    assert details == [{
        "question": "학부 수강신청 기간도 확인할까요?",
        "source_refs": [transported_ref],
    }]


def test_source_bounded_fallback_uses_exact_title_without_inventing_claims():
    sources = [{
        "source": "schedule",
        "title": "2026학년도 2학기 개강",
        "snippet": "2026학년도 2학기 개강일은 2026년 9월 1일입니다.",
        "metadata": {"campus_scope": "shared"},
    }]

    questions = source_bounded_followup_fallback(
        question="2026학년도 2학기 개강일이 언제야?",
        answer="2026학년도 2학기 개강일은 9월 1일입니다.",
        source_context=sources,
        campus_scope="seoul_bmc",
        supported_domains=["schedule"],
    )

    assert questions == ["2026학년도 2학기 개강도 자세히 확인할까요?"]
    assert build_followup_question_details(questions, sources)[0]["source_refs"] == [
        source_reference(sources[0])
    ]


def test_source_reference_is_deterministic_and_rejects_tampered_transport():
    source = {
        **SOURCES[0],
        "chunk_id": "notice-1#0",
        "url": "https://www.dongguk.edu/notice/1",
        "published_at": "2026-07-20",
    }
    first = source_reference(source)
    assert first == source_reference(dict(source))
    assert normalized_source_contract({**source, "source_ref": first})["id"] == first

    with pytest.raises(ValueError, match="does not match"):
        normalized_source_contract({**source, "snippet": "변조된 본문", "source_ref": first})
