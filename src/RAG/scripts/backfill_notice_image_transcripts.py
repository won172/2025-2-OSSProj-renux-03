"""이미지만 있는 공지에 로컬 OCR 전사문을 소급 반영한다.

두 단계로 나뉜다.

1. ``build-cache``: OCR 결과(JSONL, 이미지 SHA-256 단위)를 수집기가 읽는 SHA 캐시
   (``src/resources/notice_image_transcripts.json``)에 병합한다. 기존 항목은 덮어쓰지
   않는다. 이 캐시가 있으면 공지를 재수집해도 같은 이미지는 OCR 없이 같은 전사문이 된다.
2. ``apply``: 캐시를 이용해 정본 레코드(``SourceDocument.normalized_payload_json``)의
   ``content_text``와 ``attachments``에 전사문을 추가한다. 수집기와 같은 함수
   (``append_notice_image_text``)와 같은 첨부 형식을 써서, 재수집 결과와 글자 단위로
   같게 만든다. ``content_html``과 원본 레코드(``raw_payload_json``)는 건드리지 않는다.
   ``--apply-index``를 주면 바뀐 공지만 증분으로 청크·Chroma를 갱신하고
   parquet·BM25·FTS를 다시 만든다.

재실행해도 안전하다. 본문이나 첨부에 이미 같은 SHA-256이 있으면 그 이미지는 건너뛴다.

    python scripts/backfill_notice_image_transcripts.py build-cache --ocr-jsonl transcripts.jsonl
    python scripts/backfill_notice_image_transcripts.py apply --source-ids ids.txt --dry-run
    python scripts/backfill_notice_image_transcripts.py apply --source-ids ids.txt --apply-index
    python scripts/backfill_notice_image_transcripts.py apply --all --apply-index
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.database import Notice, SessionLocal, SourceDocument, kst_now  # noqa: E402
from src.pipelines.canonical import canonical_json  # noqa: E402
from src.services.notice_image_text import (  # noqa: E402
    TRANSCRIPT_PATH,
    NoticeImageText,
    append_notice_image_text,
    extract_official_image_urls,
)

OCR_METHOD = "paddleocr_vl_1.6+apple_vision"
INDEX_BATCH = 200  # 인덱싱이 중간에 실패해도 다시 처리할 범위를 줄인다
TRANSCRIPT_NAME = "본문 이미지 전사"
# ☎ 같은 아이콘이 숫자로 읽혀 전화번호 앞에 붙는다("☎02-2155-8819" → "802-2155-8819").
# 뒤에 오는 부분이 온전한 국내 전화번호일 때만 앞의 1~2자리를 떼어낸다.
_ICON_DIGIT_BEFORE_PHONE = re.compile(
    r"(?<![\d-])\d{1,2}(?=(0\d{1,2}-\d{3,4}-\d{4}|1\d{3}-\d{4})(?![\d-]))"
)


# 비전 언어 모델이 가끔 같은 조각을 수천 번 반복 출력한다("2222…", "대대대…", "人，人，…").
# 띄어쓰기가 없어 청크 분할도 안 되고, 임베딩 입력이 수천 토큰으로 불어나 MPS에서
# 32GB 버퍼를 요구하며 실패했다(14,580청크 중 3건). 1~20자 단위가 8번 넘게 이어지면
# 3번만 남긴다. 서식의 빈칸 줄("___ ___")도 같이 줄지만 의미는 잃지 않는다.
_REPETITION_LOOP = re.compile(r"(.{1,20}?)\1{7,}", re.S)  # 결합 문자가 섞인 반복은 단위가 8자를 넘는다


def _collapse_repetition(match: re.Match[str]) -> str:
    return match.group(1) * 3 if len(match.group(0)) >= 40 else match.group(0)


def clean_transcript(text: str) -> str:
    text = _REPETITION_LOOP.sub(_collapse_repetition, str(text or ""))
    return _ICON_DIGIT_BEFORE_PHONE.sub("", text).strip()


def clean_cache(cache_path: Path = TRANSCRIPT_PATH) -> dict[str, int]:
    """이미 만든 캐시의 기계 전사 항목에 정리 규칙을 다시 적용한다(사람이 검증한 항목은 제외)."""
    cache = load_cache(cache_path)
    changed = 0
    for record in cache.values():
        if record.get("method") != OCR_METHOD:
            continue
        cleaned = clean_transcript(record.get("text", ""))
        if cleaned != record.get("text"):
            record["text"] = cleaned
            changed += 1
    _write_json_atomic(cache_path, cache)
    return {"changed": changed, "total": len(cache)}


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp, 0o644)  # mkstemp는 0600으로 만든다 — 컨테이너 사용자도 읽을 수 있어야 한다
    os.replace(tmp, path)


def load_cache(path: Path = TRANSCRIPT_PATH) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_cache(ocr_jsonl: Path, cache_path: Path, manifest: Path | None = None) -> dict[str, int]:
    cache = load_cache(cache_path)
    # 같은 이미지(같은 SHA-256)를 다른 URL로 다시 올린 공지가 있다. 첫 공지의 URL만
    # 남기면 apply가 나머지 공지를 찾지 못하므로, 수집 목록에서 URL을 전부 모은다.
    urls_by_digest: dict[str, set[str]] = {}
    if manifest is not None:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            for image in json.loads(line).get("images", []):
                urls_by_digest.setdefault(image["sha256"], set()).add(image["url"])
    best: dict[str, dict] = {}
    for line in ocr_jsonl.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        text = clean_transcript(row.get("text", ""))
        if len(text) >= len(best.get(row["sha256"], {}).get("text", "")):
            best[row["sha256"]] = {**row, "text": text}
    counts = {"added": 0, "kept_existing": 0, "too_short": 0}
    for digest, row in best.items():
        if len(row["text"]) < config.RAG_NOTICE_IMAGE_OCR_MIN_TEXT_CHARS:
            counts["too_short"] += 1  # 장식 이미지 조각 — 캐시에 넣으면 재수집 때 잡음이 붙는다
            continue
        if digest in cache:
            counts["kept_existing"] += 1  # 사람이 검증한 기존 항목을 기계 전사로 덮지 않는다
            continue
        cache[digest] = {
            "text": row["text"],
            "method": OCR_METHOD,
            "image_url": row["image_url"],
            "image_urls": sorted(urls_by_digest.get(digest, set()) | {row["image_url"]}),
            "source_id": row["source_id"],
            "transcribed_as_of": date.today().isoformat(),
        }
        counts["added"] += 1
    _write_json_atomic(cache_path, cache)
    counts["total"] = len(cache)
    return counts


def _hash_notice_content(normalized: dict) -> str:
    from src.pipelines.notices_sync import _hash_notice_content as notice_hash

    return notice_hash(normalized)


def enrich_normalized(normalized: dict, cache: dict[str, dict], url_to_digest: dict[str, str]) -> list[NoticeImageText]:
    """정본 레코드에 아직 없는 전사문만 추가하고, 추가한 이미지를 돌려준다."""
    content = str(normalized.get("content_text") or "")
    attachments = list(normalized.get("attachments") or [])
    known = {str(item.get("sha256")) for item in attachments if isinstance(item, dict) and item.get("sha256")}
    urls = extract_official_image_urls(
        normalized.get("content_html") or "", detail_url=normalized.get("detail_url") or ""
    )[: config.RAG_NOTICE_IMAGE_CACHE_MAX_IMAGES]
    new: list[NoticeImageText] = []
    for url in urls:
        digest = url_to_digest.get(url)
        record = cache.get(digest or "")
        if not digest or not record or not str(record.get("text") or "").strip():
            continue
        if digest in known or f"SHA-256: {digest}" in content:
            continue  # 이미 반영됨 — 재실행해도 중복되지 않는다
        known.add(digest)
        new.append(NoticeImageText(
            text=str(record["text"]).strip(),
            image_url=url,
            sha256=digest,
            method=str(record.get("method") or "verified_sha256_cache"),
        ))
    if new:
        normalized["content_text"] = append_notice_image_text(content, tuple(new))
        attachments.extend(
            {"name": TRANSCRIPT_NAME, "url": i.image_url, "sha256": i.sha256, "extraction_method": i.method}
            for i in new
        )
        normalized["attachments"] = attachments
        normalized["content_hash"] = _hash_notice_content(normalized)
    return new


def _strip_backfilled_transcripts(normalized: dict) -> bool:
    """이 스크립트가 붙인 전사만 걷어낸다. 다른 방식의 전사가 섞여 있으면 손대지 않는다."""
    ours = [a for a in normalized.get("attachments") or [] if isinstance(a, dict) and a.get("name") == TRANSCRIPT_NAME]
    if not ours or any(a.get("extraction_method") != OCR_METHOD for a in ours):
        return False
    content = str(normalized.get("content_text") or "")
    marker = f"[{TRANSCRIPT_NAME} · 방식: {OCR_METHOD}"
    # 순서가 중요하다. 본문이 비어 전사로 바로 시작하는 공지에서 "\n\n{marker}"를 먼저 찾으면
    # 두 번째 전사부터 잘려 첫 번째 전사가 본문으로 남는다.
    if content.startswith(marker):
        base = ""
    elif f"\n\n{marker}" in content:
        base = content.split(f"\n\n{marker}", 1)[0]
    else:
        base = content
    normalized["content_text"] = base.strip()
    normalized["attachments"] = [a for a in normalized["attachments"] if not (isinstance(a, dict) and a.get("name") == TRANSCRIPT_NAME)]
    return True


def apply(source_ids: list[str] | None, *, dry_run: bool, apply_index: bool, report: Path | None, rebuild_changed: bool = False) -> dict:
    cache = load_cache()
    url_to_digest = {
        str(url): digest
        for digest, record in cache.items()
        for url in (record.get("image_urls") or [record.get("image_url")])
        if url
    }
    session = SessionLocal()
    result = {"applied": [], "already": [], "no_transcript": [], "failed": [], "images_added": 0, "pending_index": []}
    try:
        query = session.query(SourceDocument).filter(
            SourceDocument.dataset == "notices",
            SourceDocument.status.in_(["active", "updated"]),
            SourceDocument.source_type != "manual_notice",
        )
        if source_ids is not None:
            query = query.filter(SourceDocument.source_id.in_(source_ids))
        now = kst_now()
        for doc in query.all():
            try:
                normalized = json.loads(doc.normalized_payload_json or "{}")
                if rebuild_changed:
                    # 캐시 전사문이 바뀐 공지(예: 반복 폭주 정리)만 전사 구간을 다시 만든다.
                    before = normalized.get("content_text")
                    trial = json.loads(doc.normalized_payload_json or "{}")
                    if _strip_backfilled_transcripts(trial):
                        enrich_normalized(trial, cache, url_to_digest)
                        if trial.get("content_text") != before:
                            normalized = trial
                            if not dry_run:
                                doc.normalized_payload_json = canonical_json(normalized)
                                doc.content_hash = normalized["content_hash"]
                                doc.last_parsed_at = now
                            result.setdefault("rebuilt", []).append(doc.document_key)
                            result["pending_index"].append(doc.document_key)
                            continue
                had_any = f"[{TRANSCRIPT_NAME}" in str(normalized.get("content_text") or "")
                added = enrich_normalized(normalized, cache, url_to_digest)
                if not added:
                    result["already" if had_any else "no_transcript"].append(doc.source_id)
                    # 정본에는 반영됐는데 인덱싱 단계가 중간에 멈춘 경우: 공지 DB 본문이
                    # 정본과 다르면 인덱싱만 다시 한다. 이게 없으면 재실행이 "이미 반영"으로
                    # 보고 건너뛰어 정본과 검색 인덱스가 어긋난 채로 남는다.
                    if had_any:
                        current = session.query(Notice.content).filter(Notice.detail_url == normalized.get("detail_url")).scalar()
                        if current != normalized.get("content_text"):
                            result["pending_index"].append(doc.document_key)
                    continue
                if not dry_run:
                    doc.normalized_payload_json = canonical_json(normalized)
                    doc.content_hash = normalized["content_hash"]
                    doc.last_parsed_at = now
                result["applied"].append(doc.document_key)
                result["images_added"] += len(added)
            except Exception as exc:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않는다
                result["failed"].append({"source_id": doc.source_id, "error": f"{type(exc).__name__}: {exc}"})
        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        session.close()

    if apply_index and (result["applied"] or result["pending_index"]) and not dry_run:
        from src.pipelines.notices_sync import apply_notice_normalized_documents, refresh_notice_artifacts

        keys = result["applied"] + result["pending_index"]
        for start in range(0, len(keys), INDEX_BATCH):
            apply_notice_normalized_documents(document_keys=keys[start:start + INDEX_BATCH], apply_index=True)
            print(f"indexed {min(start + INDEX_BATCH, len(keys))}/{len(keys)}", flush=True)
        refresh_notice_artifacts()
        result["indexed"] = True
    if report:
        _write_json_atomic(report, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-cache")
    b.add_argument("--ocr-jsonl", type=Path, required=True)
    b.add_argument("--cache", type=Path, default=TRANSCRIPT_PATH)
    b.add_argument("--manifest", type=Path, help="공지별 이미지 URL·SHA 목록(JSONL)")
    a = sub.add_parser("apply")
    target = a.add_mutually_exclusive_group(required=True)
    target.add_argument("--source-ids", type=Path, help="한 줄에 source_id 하나")
    target.add_argument("--all", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--apply-index", action="store_true")
    a.add_argument("--report", type=Path)
    a.add_argument("--rebuild-changed", action="store_true", help="캐시 전사문이 바뀐 공지의 전사 구간을 다시 만든다")
    sub.add_parser("clean-cache")
    args = parser.parse_args()

    if args.command == "build-cache":
        print(json.dumps(build_cache(args.ocr_jsonl, args.cache, args.manifest), ensure_ascii=False))
        return
    if args.command == "clean-cache":
        print(json.dumps(clean_cache(), ensure_ascii=False))
        return
    ids = None if args.all else [l.strip() for l in args.source_ids.read_text().splitlines() if l.strip()]
    res = apply(ids, dry_run=args.dry_run, apply_index=args.apply_index, report=args.report, rebuild_changed=args.rebuild_changed)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
