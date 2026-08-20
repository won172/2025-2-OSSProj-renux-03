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
