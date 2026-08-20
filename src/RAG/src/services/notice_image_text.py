"""공지 본문 이미지의 텍스트를 출처 해시와 함께 보강한다."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src import config


logger = logging.getLogger(__name__)
TRANSCRIPT_PATH = Path(__file__).resolve().parents[1] / "resources" / "notice_image_transcripts.json"
ALLOWED_IMAGE_HOSTS = {"www.dongguk.edu", "dongguk.edu"}


@dataclass(frozen=True)
class NoticeImageText:
    text: str
    image_url: str
    sha256: str
    method: str


def _load_transcripts(path: Path = TRANSCRIPT_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("notice image transcript cache must be an object")
    return {
        str(digest): dict(record)
        for digest, record in payload.items()
        if isinstance(record, Mapping)
    }


def extract_official_image_urls(content_html: str, *, detail_url: str) -> list[str]:
    soup = BeautifulSoup(str(content_html or ""), "lxml")
    urls: list[str] = []
    for image in soup.find_all("img", src=True):
        absolute = urljoin(detail_url, str(image.get("src") or "").strip())
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
            continue
        if absolute not in urls:
            urls.append(absolute)
    return urls


def _response_image_bytes(response: Any, *, max_bytes: int) -> tuple[bytes, str]:
    response.raise_for_status()
    payload = bytes(getattr(response, "content", b"") or b"")
    if not payload or len(payload) > max_bytes:
        raise ValueError("notice image is empty or exceeds the size limit")
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    detected = ""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif payload.startswith((b"GIF87a", b"GIF89a")):
        detected = "image/gif"
    elif payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        detected = "image/webp"
    if not content_type.startswith("image/") and content_type != "image" and not detected:
        raise ValueError("notice image response is not an image")
    return payload, detected or content_type


def _transcribe_with_openai(
    payload: bytes,
    content_type: str,
    *,
    client: Any | None = None,
) -> str:
    if not config.OPENAI_API_KEY:
        return ""
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
    mime = content_type or mimetypes.guess_type("image.png")[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
    response = client.chat.completions.create(
        model=config.RAG_NOTICE_IMAGE_OCR_MODEL,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "이 이미지는 대학 공식 공지의 본문입니다. 보이는 한국어와 숫자를 "
                            "표의 행·열 관계, 날짜, 시간, 단서가 보존되도록 그대로 전사하세요. "
                            "추측하거나 요약하지 말고 읽을 수 없는 부분은 [판독불가]로 쓰세요."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            }
        ],
    )
    text = str(response.choices[0].message.content or "").strip()
    return text if len(text) >= config.RAG_NOTICE_IMAGE_OCR_MIN_TEXT_CHARS else ""


def collect_notice_image_text(
    content_html: str,
    *,
    detail_url: str,
    http_get: Callable[..., Any],
    timeout: float,
    transcripts: Mapping[str, Mapping[str, str]] | None = None,
    openai_client: Any | None = None,
) -> tuple[NoticeImageText, ...]:
    """공식 호스트 이미지만 제한적으로 내려받아 캐시/OCR 텍스트를 반환한다."""
    try:
        known = dict(transcripts) if transcripts is not None else _load_transcripts()
    except Exception as exc:  # noqa: BLE001 - 캐시 손상도 원문 공지 수집을 막지 않는다.
        logger.warning("Notice image transcript cache could not be loaded: %s", exc)
        known = {}
    results: list[NoticeImageText] = []
    urls = extract_official_image_urls(content_html, detail_url=detail_url)
    for image_url in urls[: config.RAG_NOTICE_IMAGE_OCR_MAX_IMAGES]:
        try:
            response = http_get(image_url, timeout=timeout)
            payload, content_type = _response_image_bytes(
                response,
                max_bytes=config.RAG_NOTICE_IMAGE_OCR_MAX_BYTES,
            )
            digest = hashlib.sha256(payload).hexdigest()
            cached = known.get(digest) or {}
            text = str(cached.get("text") or "").strip()
            method = "verified_sha256_cache" if text else ""
            if not text and config.RAG_NOTICE_IMAGE_OCR_ENABLED:
                text = _transcribe_with_openai(
                    payload,
                    content_type,
                    client=openai_client,
                )
                method = "openai_vision" if text else ""
            if text:
                results.append(
                    NoticeImageText(
                        text=text,
                        image_url=image_url,
                        sha256=digest,
                        method=method,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 이미지 하나가 공지 본문 수집을 막지 않는다.
            logger.warning("Notice image text extraction failed for %s: %s", image_url, exc)
    return tuple(results)


def append_notice_image_text(content_text: str, images: tuple[NoticeImageText, ...]) -> str:
    body = str(content_text or "").strip()
    sections = [body] if body else []
    for image in images:
        sections.append(
            "\n".join(
                [
                    f"[본문 이미지 전사 · 방식: {image.method} · SHA-256: {image.sha256}]",
                    image.text,
                ]
            )
        )
    return "\n\n".join(section for section in sections if section).strip()


__all__ = [
    "NoticeImageText",
    "append_notice_image_text",
    "collect_notice_image_text",
    "extract_official_image_urls",
]
