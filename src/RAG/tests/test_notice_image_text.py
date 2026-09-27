from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.services.notice_image_text import (  # noqa: E402
    append_notice_image_text,
    collect_notice_image_text,
    extract_official_image_urls,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"official-image-bytes"


class FakeImageResponse:
    content = PNG
    headers = {"Content-Type": "image;charset=UTF-8"}

    def raise_for_status(self):
        return None


class FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": "지원서 접수: 2026. 8. 5. ~ 8. 13. 14시"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class FakeOpenAI:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": FakeCompletions()})()


def test_only_official_https_images_are_selected_and_deduplicated():
    html = """
    <img src="/cmmn/fileView?physical=one.png" />
    <img src="/cmmn/fileView?physical=one.png" />
    <img src="http://www.dongguk.edu/insecure.png" />
    <img src="https://evil.example/poster.png" />
    """
    assert extract_official_image_urls(
        html,
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
    ) == ["https://www.dongguk.edu/cmmn/fileView?physical=one.png"]


def test_verified_sha_cache_works_without_openai_and_preserves_lineage(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    digest = hashlib.sha256(PNG).hexdigest()
    images = collect_notice_image_text(
        '<img src="/poster.png" />',
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda *_args, **_kwargs: FakeImageResponse(),
        timeout=3,
        transcripts={digest: {"text": "접수: 2026. 8. 5. ~ 8. 13. 14시"}},
    )
    assert len(images) == 1
    assert images[0].method == "verified_sha256_cache"
    assert images[0].sha256 == digest
    enriched = append_notice_image_text("기존 본문", images)
    assert "기존 본문" in enriched
    assert digest in enriched
    assert "8. 13. 14시" in enriched


def test_cache_miss_uses_configured_vision_model_with_data_url(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "RAG_NOTICE_IMAGE_OCR_ENABLED", True)
    monkeypatch.setattr(config, "RAG_NOTICE_IMAGE_OCR_MODEL", "vision-test")
    client = FakeOpenAI()

    images = collect_notice_image_text(
        '<img src="/poster.png" />',
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda *_args, **_kwargs: FakeImageResponse(),
        timeout=3,
        transcripts={},
        openai_client=client,
    )

    assert images[0].method == "openai_vision"
    call = client.chat.completions.calls[0]
    assert call["model"] == "vision-test"
    image_part = call["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_bad_image_response_is_skipped_without_losing_original_text():
    class NotImage(FakeImageResponse):
        content = b"<html>error</html>"
        headers = {"Content-Type": "text/html"}

    images = collect_notice_image_text(
        '<img src="/poster.png" />',
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda *_args, **_kwargs: NotImage(),
        timeout=3,
        transcripts={},
    )
    assert images == ()
    assert append_notice_image_text("기존 본문", images) == "기존 본문"


class _SizedImageResponse:
    """바이트 수를 지정할 수 있는 이미지 응답(모든 응답이 서로 다른 해시를 갖게 한다)."""

    def __init__(self, tag: str, size: int = 0):
        self.content = PNG + tag.encode() + b"0" * size
        self.headers = {"Content-Type": "image/png"}

    def raise_for_status(self):
        return None


def test_cache_hits_are_used_beyond_the_paid_ocr_image_limit(monkeypatch):
    """백필 캐시는 3장째 이후 이미지도 담고 있다. 재수집이 OCR 상한(2장)을 캐시 조회에까지
    적용하면 이미 확보한 전사문이 재수집 때 사라진다."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "RAG_NOTICE_IMAGE_OCR_MAX_IMAGES", 2)
    responses = {f"https://www.dongguk.edu/p{i}.png": _SizedImageResponse(f"p{i}") for i in range(4)}
    transcripts = {
        hashlib.sha256(r.content).hexdigest(): {"text": f"{i}번째 포스터 전사문입니다. 신청 기간 안내"}
        for i, r in enumerate(responses.values())
    }
    images = collect_notice_image_text(
        "".join(f'<img src="/p{i}.png" />' for i in range(4)),
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda url, **_kwargs: responses[url],
        timeout=3,
        transcripts=transcripts,
    )
    assert [image.image_url for image in images] == list(responses)


def test_large_cached_image_is_used_but_large_cache_miss_is_not_sent_to_ocr(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "RAG_NOTICE_IMAGE_OCR_ENABLED", True)
    monkeypatch.setattr(config, "RAG_NOTICE_IMAGE_OCR_MAX_BYTES", 100)
    big_cached = _SizedImageResponse("cached", size=500)
    big_miss = _SizedImageResponse("miss", size=500)
    client = FakeOpenAI()
    images = collect_notice_image_text(
        '<img src="/a.png" /><img src="/b.png" />',
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda url, **_kwargs: big_cached if url.endswith("/a.png") else big_miss,
        timeout=3,
        transcripts={hashlib.sha256(big_cached.content).hexdigest(): {"text": "고해상도 포스터 전사문입니다. 문의 02-2260-3114"}},
        openai_client=client,
    )
    assert [image.image_url.rsplit("/", 1)[-1] for image in images] == ["a.png"]
    assert client.chat.completions.calls == []  # 5MB 상한을 넘는 캐시 미스는 유료 OCR로 보내지 않는다


def test_cache_record_method_is_preserved_for_reproducible_recollection(monkeypatch):
    """백필이 쓴 본문과 재수집 결과가 같아야 content_hash가 바뀌지 않는다."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    digest = hashlib.sha256(PNG).hexdigest()
    images = collect_notice_image_text(
        '<img src="/poster.png" />',
        detail_url="https://www.dongguk.edu/article/NOTICE/detail/1",
        http_get=lambda *_args, **_kwargs: FakeImageResponse(),
        timeout=3,
        transcripts={digest: {"text": "접수: 2026. 8. 5. ~ 8. 13. 14시", "method": "paddleocr_vl_1.6+apple_vision"}},
    )
    assert images[0].method == "paddleocr_vl_1.6+apple_vision"
    assert "방식: paddleocr_vl_1.6+apple_vision" in append_notice_image_text("", images)
