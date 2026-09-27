"""Release-gate policy for the real-candidate golden evaluation.

Two decisions live here so they can be unit tested instead of hidden in
workflow bash:

* ``endpoint``: whether the candidate endpoint is configured for this run.
  Only ``pull_request`` runs may skip without an endpoint, and such a skip is
  reported as "NOT release evidence". Every other event (``workflow_dispatch``
  is the release run) fails when the endpoint is missing, so a release run can
  never go green by skipping.
* ``manifest``: whether a run manifest may be used as release evidence. Subset
  (``--case-id``) runs, incomplete runs, runs with failed cases, and runs the
  runner itself marked ``release_eligible: false`` are rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from golden_matrix import load_matrix

NON_BLOCKING_EVENTS = frozenset({"pull_request"})
ENDPOINT_ENV = "RAG_GOLDEN_BASE_URL"

SKIP_SUMMARY = (
    "### RAG 골든 후보 검증: **건너뜀 — NOT release evidence**\n"
    "\n"
    "`RAG_GOLDEN_BASE_URL` 시크릿이 없어 실제 후보 평가를 실행하지 않았습니다.\n"
    "이 green 결과는 출시 근거가 아닙니다(NOT release evidence).\n"
    "배포 후보를 띄우고 시크릿을 설정한 뒤 이 워크플로를 수동 실행(workflow_dispatch)하세요.\n"
)
MISSING_ENDPOINT_SUMMARY = (
    "### RAG 골든 후보 검증: **실패 — 후보 endpoint 없음**\n"
    "\n"
    "`{event}` 실행은 출시 게이트이므로 `RAG_GOLDEN_BASE_URL` 없이 통과할 수 없습니다.\n"
    "배포 후보 endpoint를 시크릿으로 설정한 뒤 다시 실행하세요.\n"
)


def endpoint_decision(event_name: str, base_url: str | None) -> tuple[bool, int, str]:
    """Return ``(configured, exit_code, summary_markdown)`` for a workflow event."""
    if (base_url or "").strip():
        return True, 0, ""
    if event_name in NON_BLOCKING_EVENTS:
        return False, 0, SKIP_SUMMARY
    return False, 1, MISSING_ENDPOINT_SUMMARY.format(event=event_name or "unknown")


def release_manifest_problems(
    manifest: Mapping[str, Any],
    expected_case_ids: Iterable[str] | None = None,
) -> list[str]:
    """List every reason a run manifest is not acceptable as release evidence."""
    if not isinstance(manifest, Mapping):
        return ["manifest is not a JSON object"]
    problems: list[str] = []
    if manifest.get("release_eligible") is not True:
        problems.append("release_eligible is not true")
    if manifest.get("complete") is not True:
        problems.append("complete is not true")
    if manifest.get("selected_case_ids"):
        problems.append(
            "partial case selection (--case-id subset run) is never release evidence"
        )
    if manifest.get("failed_case_ids"):
        problems.append(f"failed cases present: {len(manifest['failed_case_ids'])}")
    if manifest.get("candidate_fingerprint_stable") is not True:
        problems.append("candidate fingerprint was not stable during the run")
    result_count = manifest.get("result_count")
    expected_count = manifest.get("expected_result_count")
    if (
        not _is_count(result_count)
        or not _is_count(expected_count)
        or result_count != expected_count
    ):
        problems.append(
            f"result_count {result_count!r} does not equal expected_result_count {expected_count!r}"
        )
    if expected_case_ids is not None:
        expected = len(set(expected_case_ids))
        if expected_count != expected:
            problems.append(
                f"expected_result_count {expected_count!r} does not cover the matrix ({expected} cases)"
            )
    return problems


def _is_count(value: Any) -> bool:
    # bool is an int subclass; True must never pass as a count of 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _append(path_value: str | None, text: str) -> None:
    if not path_value or not text:
        return
    with Path(path_value).open("a", encoding="utf-8") as handle:
        handle.write(text)


def _cmd_endpoint(args: argparse.Namespace) -> int:
    configured, code, summary = endpoint_decision(args.event_name, os.environ.get(ENDPOINT_ENV))
    _append(os.environ.get("GITHUB_OUTPUT"), f"configured={'true' if configured else 'false'}\n")
    _append(os.environ.get("GITHUB_STEP_SUMMARY"), summary)
    if configured:
        print("Golden gate: candidate endpoint configured; running real evaluation.")
    elif code == 0:
        print(
            "::notice title=골든 게이트 건너뜀 (NOT release evidence)::"
            "RAG_GOLDEN_BASE_URL이 없어 실제 후보 검증을 건너뜁니다. 이 결과는 출시 근거가 아닙니다."
        )
    else:
        print(
            f"::error title=골든 릴리스 게이트 실패::{args.event_name} 실행에는 "
            "RAG_GOLDEN_BASE_URL(배포 후보 endpoint)이 필요합니다."
        )
    return code


def _cmd_manifest(args: argparse.Namespace) -> int:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Golden release gate FAIL: cannot read manifest: {exc}", file=sys.stderr)
        return 2
    try:
        case_ids = [case.id for case in load_matrix(args.matrix)]
    except (OSError, ValueError, KeyError) as exc:
        print(f"Golden release gate FAIL: cannot load matrix: {exc}", file=sys.stderr)
        return 2
    problems = release_manifest_problems(manifest, case_ids)
    if problems:
        print(
            "Golden release gate FAIL (NOT release evidence): " + "; ".join(problems),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({"release_evidence": True, "run_id": manifest.get("run_id")}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden evaluation release-gate policy")
    sub = parser.add_subparsers(dest="command", required=True)
    endpoint = sub.add_parser("endpoint", help="Decide whether a missing endpoint skips or fails")
    endpoint.add_argument("--event-name", required=True)
    endpoint.set_defaults(func=_cmd_endpoint)
    manifest = sub.add_parser("manifest", help="Reject manifests that are not release evidence")
    manifest.add_argument("--manifest", type=Path, required=True)
    manifest.add_argument(
        "--matrix",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "golden_matrix.csv",
    )
    manifest.set_defaults(func=_cmd_manifest)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
