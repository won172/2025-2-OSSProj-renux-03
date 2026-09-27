"""Verify that a stored replay is complete, schema-valid, and provenance-bound."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from check_golden_release_gate import release_manifest_problems
from evaluate_golden_matrix import load_results
from golden_matrix import TAXONOMY_VERSION, file_sha256, load_matrix, load_taxonomy, validate_matrix


def _validate_json(path: Path, schema_path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "<root>"
        raise ValueError(f"{path}:{location}: {error.message}")
    return data


def main(argv: list[str] | None = None) -> int:
    rag_root = Path(__file__).resolve().parents[1]
    repo_root = rag_root.parents[1]
    parser = argparse.ArgumentParser(description="Verify a complete real-run replay and provenance hashes")
    parser.add_argument("--replay-dir", type=Path, default=rag_root / "tests" / "replay" / "latest")
    parser.add_argument("--matrix", type=Path, default=rag_root / "tests" / "golden_matrix.csv")
    parser.add_argument("--taxonomy", type=Path, default=rag_root / "tests" / "golden_taxonomy.v1.json")
    parser.add_argument("--result-schema", type=Path, default=rag_root / "tests" / "golden_result.schema.json")
    parser.add_argument("--manifest-schema", type=Path, default=rag_root / "tests" / "golden_run_manifest.schema.json")
    args = parser.parse_args(argv)
    results_path = args.replay_dir / "results.jsonl"
    manifest_path = args.replay_dir / "manifest.json"
    if not results_path.is_file() or not manifest_path.is_file():
        print(
            "Golden replay HOLD: tests/replay/latest must contain a complete real HTTP results.jsonl and manifest.json",
            file=sys.stderr,
        )
        return 3
    try:
        taxonomy = load_taxonomy(args.taxonomy)
        cases = load_matrix(args.matrix)
        errors = validate_matrix(cases, taxonomy)
        if errors:
            raise ValueError("invalid matrix: " + "; ".join(errors))
        manifest = _validate_json(manifest_path, args.manifest_schema)
        results = load_results(results_path, args.result_schema)
        problems = release_manifest_problems(manifest, (case.id for case in cases))
        if problems:
            raise ValueError(
                "manifest is not a complete full-matrix real run (NOT release evidence): "
                + "; ".join(problems)
            )
        if manifest["matrix_sha256"] != file_sha256(args.matrix):
            raise ValueError("matrix hash differs from real-run provenance")
        if manifest["taxonomy_version"] != TAXONOMY_VERSION:
            raise ValueError("taxonomy version differs from real-run provenance")
        if manifest["taxonomy_sha256"] != file_sha256(args.taxonomy):
            raise ValueError("taxonomy hash differs from real-run provenance")
        if manifest["results_sha256"] != file_sha256(results_path):
            raise ValueError("results hash differs from real-run provenance")
        if manifest["result_count"] != len(results) or len(results) != len(cases):
            raise ValueError("result count does not cover the matrix")
        if set(result["id"] for result in results) != set(case.id for case in cases):
            raise ValueError("result IDs do not exactly cover the matrix")
        if manifest["git"]["dirty"]:
            raise ValueError("real-run candidate was dirty; a release replay must be produced from a clean checkout")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
        if manifest["git"]["commit_sha"] != head:
            raise ValueError("real-run commit SHA does not match the candidate checkout")
        fingerprint = manifest["candidate_fingerprint"]
        fingerprint_hash = fingerprint["fingerprint_sha256"]
        if manifest["candidate_fingerprint_sha256"] != fingerprint_hash:
            raise ValueError("candidate fingerprint summary hash differs from attestation")
        unsigned_fingerprint = {
            key: value for key, value in fingerprint.items() if key != "fingerprint_sha256"
        }
        if hashlib.sha256(
            json.dumps(
                unsigned_fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest() != fingerprint_hash:
            raise ValueError("candidate fingerprint self-hash is invalid")
        if fingerprint["build_revision"] != head:
            raise ValueError("HTTP candidate build revision does not match the evaluated checkout")
        if fingerprint["dense_index_ready"] is not True:
            raise ValueError("candidate dense indexes were incomplete during the real run")
        if manifest["candidate_config"] != fingerprint["runtime_config"]:
            raise ValueError("candidate config was not copied from HTTP attestation")
        if manifest["data_manifest_hash"] != fingerprint["artifact_manifest_sha256"]:
            raise ValueError("artifact manifest summary differs from HTTP attestation")
        if manifest["data_files"] != fingerprint["artifacts"]:
            raise ValueError("artifact records differ from HTTP attestation")
        canonical = json.dumps(manifest["data_files"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != manifest["data_manifest_hash"]:
            raise ValueError("data manifest hash is invalid")
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"Golden replay FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"passed": True, "run_id": manifest["run_id"], "result_count": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
