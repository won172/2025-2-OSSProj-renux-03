"""Run the complete golden matrix against a real HTTP RAG candidate.

This file does not score or synthesize results.  Every result field is either a
literal HTTP response value or a deterministic hash/normalization of it.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

RAG_ROOT = Path(__file__).resolve().parents[1]
if str(RAG_ROOT) not in sys.path:
    sys.path.insert(0, str(RAG_ROOT))

from golden_matrix import TAXONOMY_VERSION, file_sha256, load_matrix, load_taxonomy, validate_matrix
from src.services.source_contract import normalized_source_contract


RUNNER_VERSION = "real-http-v4-as-of"
DEFAULT_GOLDEN_AS_OF = "2026-07-30"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo_root, stderr=subprocess.DEVNULL, text=True, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _request_json(url: str, payload: dict[str, Any] | None, timeout: float, headers: dict[str, str]) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"error": raw[:1000]}
        return exc.code, detail


def _validated_candidate_fingerprint(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        raise ValueError(f"candidate fingerprint endpoint returned HTTP {status}")
    required = {
        "schema_version",
        "build_revision",
        "answer_contract_version",
        "runtime_config",
        "artifact_manifest_sha256",
        "artifacts",
        "datasets",
        "dense_index_ready",
        "fingerprint_sha256",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"candidate fingerprint is missing fields: {missing}")
    unsigned = {key: value for key, value in payload.items() if key != "fingerprint_sha256"}
    if str(payload["fingerprint_sha256"]) != _canonical_hash(unsigned):
        raise ValueError("candidate fingerprint self-hash is invalid")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("candidate fingerprint has no artifact records")
    canonical_artifacts = json.dumps(
        artifacts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if str(payload["artifact_manifest_sha256"]) != _sha256_bytes(canonical_artifacts):
        raise ValueError("candidate artifact manifest hash is invalid")
    return payload


def _normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    return normalized_source_contract(source)


def _result_from_response(case, status: int, response: dict, latency_ms: int) -> dict[str, Any]:
    if status < 200 or status >= 300:
        return {
            "schema_version": 2,
            "status": "http_error",
            "id": case.id,
            "question_hash": _sha256_bytes(case.question.encode("utf-8")),
            "response_received": False,
            "http_status": status,
            "latency_ms": latency_ms,
            "request_id": None,
            "actual_app_intents": [],
            "answer": "",
            "citations_text": "",
            "sources": [],
            "followups": [],
            "fallback_triggered": False,
            "fallback_reason": None,
            "grounded": None,
            "grounding_score": None,
            "error": json.dumps(response, ensure_ascii=False)[:2000],
        }
    sources = [_normalize_source(item) for item in response.get("sources", []) if isinstance(item, dict)]
    details = response.get("suggested_question_details", [])
    followups: list[dict[str, Any]] = []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict) or not str(detail.get("question") or "").strip():
                continue
            refs = [str(ref) for ref in detail.get("source_refs", []) if str(ref).strip()]
            followups.append({"question": str(detail["question"]).strip(), "source_refs": refs})
    transported_questions = [
        str(question).strip()
        for question in response.get("suggested_questions", [])
        if str(question).strip()
    ]
    detailed_questions = [item["question"] for item in followups]
    if transported_questions != detailed_questions:
        # Preserve the real mismatch so the release evaluator fails instead of
        # silently inventing lineage for the legacy string-only transport.
        followups = [{"question": question, "source_refs": []} for question in transported_questions]
    return {
        "schema_version": 2,
        "status": "ok",
        "id": case.id,
        "question_hash": _sha256_bytes(case.question.encode("utf-8")),
        "response_received": True,
        "http_status": status,
        "latency_ms": latency_ms,
        "request_id": str(response.get("request_id") or "") or None,
        "actual_app_intents": [
            str(item)
            for item in response.get("resolved_intents", response.get("route", []))
        ],
        "answer": str(response.get("answer") or ""),
        "citations_text": str(response.get("citations") or ""),
        "sources": sources,
        "followups": followups,
        "fallback_triggered": bool(response.get("fallback_triggered", False)),
        "fallback_reason": response.get("fallback_reason"),
        "grounded": response.get("grounded"),
        "grounding_score": response.get("grounding_score"),
        "error": None,
    }


def _run_case(
    case,
    ask_url: str,
    timeout: float,
    headers: dict[str, str],
    as_of: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        status, response = _request_json(
            ask_url,
            {
                "question": case.question,
                "sessionId": f"golden-{uuid.uuid4()}",
                "asOf": as_of,
            },
            timeout,
            headers,
        )
        return _result_from_response(case, status, response, round((time.perf_counter() - started) * 1000))
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return _result_from_response(
            case,
            0,
            {"error": f"{type(exc).__name__}: {exc}"},
            round((time.perf_counter() - started) * 1000),
        )


def main(argv: list[str] | None = None) -> int:
    rag_root = Path(__file__).resolve().parents[1]
    repo_root = rag_root.parents[1]
    parser = argparse.ArgumentParser(description="Run real HTTP responses for every golden case")
    parser.add_argument("--base-url", default=os.getenv("RAG_GOLDEN_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--matrix", type=Path, default=rag_root / "tests" / "golden_matrix.csv")
    parser.add_argument("--taxonomy", type=Path, default=rag_root / "tests" / "golden_taxonomy.v1.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--as-of",
        default=os.getenv("RAG_GOLDEN_AS_OF", DEFAULT_GOLDEN_AS_OF),
        help="모든 케이스가 공유할 재현 가능한 기준일(YYYY-MM-DD)",
    )
    parser.add_argument("--case-id", action="append", default=[], help="Troubleshooting only; subset runs are never complete")
    parser.add_argument("--header", action="append", default=[], help="Extra HTTP header as Name=Value; values are not stored")
    args = parser.parse_args(argv)

    try:
        taxonomy = load_taxonomy(args.taxonomy)
        all_cases = load_matrix(args.matrix)
        errors = validate_matrix(all_cases, taxonomy)
        if errors:
            raise ValueError("; ".join(errors))
        headers = dict(item.split("=", 1) for item in args.header)
        date.fromisoformat(args.as_of)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Runner contract error: {exc}", file=sys.stderr)
        return 2

    selected_ids = set(args.case_id)
    cases = [case for case in all_cases if not selected_ids or case.id in selected_ids]
    if selected_ids - {case.id for case in all_cases}:
        print(f"Unknown case IDs: {sorted(selected_ids - {case.id for case in all_cases})}", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    ask_url = f"{base_url}/ask"
    run_id = str(uuid.uuid4())
    started_at = _utc_now()
    try:
        ready_status, ready_payload = _request_json(f"{base_url}/ready", None, min(args.timeout, 30), headers)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        ready_status, ready_payload = 0, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        fingerprint_status, fingerprint_payload = _request_json(
            f"{base_url}/evaluation/fingerprint",
            None,
            min(args.timeout, 120),
            headers,
        )
        candidate_fingerprint = _validated_candidate_fingerprint(
            fingerprint_status,
            fingerprint_payload,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Candidate attestation failed: {exc}", file=sys.stderr)
        return 4

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = [
            executor.submit(
                _run_case,
                case,
                ask_url,
                args.timeout,
                headers,
                args.as_of,
            )
            for case in cases
        ]
        results = [future.result() for future in futures]

    end_fingerprint_hash: str | None = None
    candidate_fingerprint_stable = False
    try:
        end_status, end_payload = _request_json(
            f"{base_url}/evaluation/fingerprint",
            None,
            min(args.timeout, 120),
            headers,
        )
        end_fingerprint = _validated_candidate_fingerprint(end_status, end_payload)
        end_fingerprint_hash = end_fingerprint["fingerprint_sha256"]
        candidate_fingerprint_stable = (
            end_fingerprint_hash == candidate_fingerprint["fingerprint_sha256"]
        )
    except (OSError, ValueError, json.JSONDecodeError):
        candidate_fingerprint_stable = False

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.jsonl"
    results_bytes = (
        "\n".join(json.dumps(result, ensure_ascii=False, sort_keys=True) for result in results) + "\n"
    ).encode("utf-8")
    results_path.write_bytes(results_bytes)
    git_diff = _git(repo_root, "diff", "--binary", "HEAD")
    commit_sha = _git(repo_root, "rev-parse", "HEAD")
    dirty = bool(_git(repo_root, "status", "--porcelain"))
    failures = [result["id"] for result in results if result["status"] != "ok"]
    complete = not selected_ids and len(results) == len(all_cases) and not failures
    release_eligible = (
        complete
        and not dirty
        and commit_sha != "unavailable"
        and candidate_fingerprint["build_revision"] == commit_sha
        and candidate_fingerprint_stable
        and candidate_fingerprint["dense_index_ready"] is True
    )
    manifest = {
        "schema_version": 2,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_started_at": started_at,
        "run_completed_at": _utc_now(),
        "endpoint": ask_url,
        "as_of": args.as_of,
        "endpoint_ready_status": ready_status,
        "endpoint_ready_payload_hash": _canonical_hash(ready_payload),
        "header_names": sorted(headers),
        "matrix_sha256": file_sha256(args.matrix),
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_sha256": file_sha256(args.taxonomy),
        "results_sha256": _sha256_bytes(results_bytes),
        "result_count": len(results),
        "expected_result_count": len(all_cases),
        "selected_case_ids": sorted(selected_ids),
        "complete": complete,
        "release_eligible": release_eligible,
        "failed_case_ids": failures,
        "git": {
            "commit_sha": commit_sha,
            "dirty": dirty,
            "diff_sha256": _sha256_bytes(git_diff.encode("utf-8")),
        },
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_fingerprint_sha256": candidate_fingerprint["fingerprint_sha256"],
        "candidate_fingerprint_end_sha256": end_fingerprint_hash,
        "candidate_fingerprint_stable": candidate_fingerprint_stable,
        # Compatibility summaries are copied from the HTTP attestation, never
        # recomputed from the runner checkout.
        "candidate_config": candidate_fingerprint["runtime_config"],
        "data_manifest_hash": candidate_fingerprint["artifact_manifest_sha256"],
        "data_files": candidate_fingerprint["artifacts"],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(args.output_dir), **manifest}, ensure_ascii=False, indent=2))
    return 0 if manifest["release_eligible"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
