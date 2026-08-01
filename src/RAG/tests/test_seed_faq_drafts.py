from __future__ import annotations

from scripts.seed_faq_drafts import FAQ_DRAFTS, count_placeholders, draft_payload


def _draft(question: str):
    return next(draft for draft in FAQ_DRAFTS if draft.question == question)


def test_all_faq_drafts_are_officially_grounded_and_have_no_placeholders():
    assert len(FAQ_DRAFTS) == 14
    assert len({draft.question for draft in FAQ_DRAFTS}) == 14
    assert all(count_placeholders(draft) == 0 for draft in FAQ_DRAFTS)
    assert all("공식 근거:" in draft.answer for draft in FAQ_DRAFTS)
    assert all("https://" in draft.answer for draft in FAQ_DRAFTS)


def test_current_rule_changes_are_not_replaced_with_stale_guide_values():
    cancellation = _draft("수강신청 취소는 어떻게 해?").answer
    assert "최소 1학점" in cancellation
    assert "최소 12학점" not in cancellation

    language = _draft("토익 졸업요건이 뭐야").answer
    assert "외국어 PASS제" in language
    assert "폐지" in language
    assert "2026년 가을 졸업" in language
    assert "TOPIK" in language


def test_time_varying_faqs_state_scope_and_verification_date():
    library = _draft("중앙도서관 평일 운영시간과 대출실 마감시간을 알려줘").answer
    assert "2026.08.02 조회 기준" in library
    assert "날짜별" in library

    exchange = _draft("2027학년도 1학기 교환학생 지원 기간이 언제야?").answer
    assert "2026.08.05.(수)~08.13.(목) 14:00" in exchange
    assert "일정도 변경될 수 있습니다" in exchange


def test_seed_payload_records_verification_date():
    payload = draft_payload(FAQ_DRAFTS[0], "test")
    assert payload["verified_as_of"] == "2026-08-02"
    assert payload["requester"] == "test"
