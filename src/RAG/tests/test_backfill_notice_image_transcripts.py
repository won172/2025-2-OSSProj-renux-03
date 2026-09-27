"""공지 이미지 전사 소급 반영 스크립트의 중복 방지·형식 계약."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.notice_image_text import NoticeImageText, append_notice_image_text  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "backfill_notice_image_transcripts", ROOT / "scripts" / "backfill_notice_image_transcripts.py"
)
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)

URL_A = "https://www.dongguk.edu/cmmn/fileView?path=/ckeditor//X&physical=a.png"
URL_B = "https://www.dongguk.edu/cmmn/fileView?path=/ckeditor//X&physical=b.png"
DIGEST_A, DIGEST_B = "a" * 64, "b" * 64
CACHE = {
    DIGEST_A: {"text": "신청기간: 2026. 8. 12.(수) ~ 9. 9.(수) 18시", "method": "paddleocr_vl_1.6+apple_vision"},
    DIGEST_B: {"text": "문의: 02-2260-3114 학생지원팀", "method": "paddleocr_vl_1.6+apple_vision"},
}


def _normalized(content_text: str = "") -> dict:
    return {
        "title": "장학금 안내",
        "category": "장학",
        "published_at": "2026-08-12",
        "detail_url": "https://www.dongguk.edu/article/JANGHAKNOTICE/detail/1",
        "content_html": f'<p><img src="{URL_A}"/><img src="{URL_B}"/></p>',
        "content_text": content_text,
        "attachments": [{"name": "요강.hwp", "url": "https://www.dongguk.edu/f.hwp"}],
    }


def test_enrich_matches_crawler_format_and_preserves_html():
    normalized = _normalized()
    html = normalized["content_html"]
    added = backfill.enrich_normalized(normalized, CACHE, {URL_A: DIGEST_A, URL_B: DIGEST_B})

    assert [image.sha256 for image in added] == [DIGEST_A, DIGEST_B]
    # 수집기와 같은 함수로 만든 본문이어야 재수집 결과와 글자 단위로 같다.
    expected = append_notice_image_text("", tuple(
        NoticeImageText(text=CACHE[d]["text"], image_url=u, sha256=d, method=CACHE[d]["method"])
        for u, d in ((URL_A, DIGEST_A), (URL_B, DIGEST_B))
    ))
    assert normalized["content_text"] == expected
    assert normalized["content_html"] == html
    assert normalized["attachments"][0]["name"] == "요강.hwp"
    assert [a["sha256"] for a in normalized["attachments"][1:]] == [DIGEST_A, DIGEST_B]
    assert normalized["content_hash"]


def test_enrich_is_idempotent():
    normalized = _normalized()
    mapping = {URL_A: DIGEST_A, URL_B: DIGEST_B}
    backfill.enrich_normalized(normalized, CACHE, mapping)
    first = dict(normalized)
    assert backfill.enrich_normalized(normalized, CACHE, mapping) == []
    assert normalized == first
    assert normalized["content_text"].count("[본문 이미지 전사") == 2


def test_enrich_skips_images_already_transcribed_by_the_crawler():
    existing = append_notice_image_text("기존 본문", (
        NoticeImageText(text="이미 전사된 내용입니다", image_url=URL_A, sha256=DIGEST_A, method="openai_vision"),
    ))
    normalized = _normalized(existing)
    added = backfill.enrich_normalized(normalized, CACHE, {URL_A: DIGEST_A, URL_B: DIGEST_B})
    assert [image.sha256 for image in added] == [DIGEST_B]
    assert normalized["content_text"].startswith("기존 본문")
    assert normalized["content_text"].count(f"SHA-256: {DIGEST_A}") == 1


def test_icon_digit_before_phone_number_is_removed_only_when_the_rest_is_a_phone():
    assert backfill.clean_transcript("서초구청 802-2155-8819") == "서초구청 02-2155-8819"
    assert backfill.clean_transcript("상담 21599-2000") == "상담 1599-2000"
    assert backfill.clean_transcript("문의 02-2260-3114") == "문의 02-2260-3114"
    assert backfill.clean_transcript("2026-08-12 접수") == "2026-08-12 접수"


def test_repetition_loops_from_the_vision_model_are_collapsed():
    """반복 폭주가 남으면 분할도 안 되는 수천 자짜리 청크가 되어 임베딩이 실패했다."""
    looped = "신청기간 안내 " + "2" * 4000 + " 문의 02-2260-3114"
    cleaned = backfill.clean_transcript(looped)
    assert "2" * 4 not in cleaned.replace("2260", "").replace("3114", "")
    assert cleaned.startswith("신청기간 안내") and cleaned.endswith("02-2260-3114")
    assert backfill.clean_transcript("人，" * 500) == "人，" * 3
    # 짧은 정상 반복(표의 빈칸, 날짜 구분자)은 그대로 둔다
    assert backfill.clean_transcript("ㅁㅁ ㅁㅁ 2026.08.12") == "ㅁㅁ ㅁㅁ 2026.08.12"


def test_strip_only_removes_transcripts_this_script_added():
    normalized = _normalized("본문")
    backfill.enrich_normalized(normalized, CACHE, {URL_A: DIGEST_A, URL_B: DIGEST_B})
    assert backfill._strip_backfilled_transcripts(normalized) is True
    assert normalized["content_text"] == "본문"
    assert [a["name"] for a in normalized["attachments"]] == ["요강.hwp"]

    crawler_made = _normalized(append_notice_image_text("본문", (
        NoticeImageText(text="운영 OCR 전사입니다", image_url=URL_A, sha256=DIGEST_A, method="openai_vision"),
    )))
    crawler_made["attachments"].append({"name": "본문 이미지 전사", "url": URL_A, "sha256": DIGEST_A, "extraction_method": "openai_vision"})
    assert backfill._strip_backfilled_transcripts(crawler_made) is False


def test_strip_handles_multi_image_notices_that_start_with_a_transcript():
    """본문이 비어 전사로 시작하는 공지에서 첫 번째 전사가 남던 버그의 회귀 테스트."""
    normalized = _normalized("")
    backfill.enrich_normalized(normalized, CACHE, {URL_A: DIGEST_A, URL_B: DIGEST_B})
    assert backfill._strip_backfilled_transcripts(normalized) is True
    assert normalized["content_text"] == ""


def test_long_unit_repetition_with_combining_marks_is_collapsed():
    looped = "안내 " + "་ཁྲིམས་ཀྱི" * 300
    assert len(backfill.clean_transcript(looped)) < 60
