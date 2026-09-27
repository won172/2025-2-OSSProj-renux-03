"""Release-gate policy for the real-candidate golden evaluation.

No network, endpoint, or model calls: manifests are in-memory/temp fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = RAG_ROOT / "scripts"
WORKFLOW = RAG_ROOT.parents[1] / ".github" / "workflows" / "rag-golden-release.yml"
MATRIX = RAG_ROOT / "tests" / "golden_matrix.csv"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_golden_release_gate as gate  # noqa: E402
import evaluate_golden_matrix  # noqa: E402
import verify_golden_replay  # noqa: E402
from golden_matrix import load_matrix  # noqa: E402


@pytest.fixture(scope="module")
def case_ids() -> list[str]:
    return [case.id for case in load_matrix(MATRIX)]


@pytest.fixture
def release_manifest(case_ids) -> dict:
    return {
        "run_id": "fixture-run",
        "as_of": "2026-07-30",
        "run_completed_at": "2026-07-30T00:00:00+00:00",
        "release_eligible": True,
        "complete": True,
        "selected_case_ids": [],
        "failed_case_ids": [],
        "candidate_fingerprint_stable": True,
        "result_count": len(case_ids),
        "expected_result_count": len(case_ids),
    }


# --- endpoint decision -------------------------------------------------------


def test_pull_request_without_endpoint_skips_but_is_not_release_evidence():
    configured, code, summary = gate.endpoint_decision("pull_request", "")
    assert configured is False
    assert code == 0
    assert "NOT release evidence" in summary


@pytest.mark.parametrize("event", ["workflow_dispatch", "push", "release", "schedule", ""])
def test_release_run_without_endpoint_fails(event):
    configured, code, summary = gate.endpoint_decision(event, None)
    assert configured is False
    assert code != 0
    assert "RAG_GOLDEN_BASE_URL" in summary


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_whitespace_endpoint_is_not_configured(event):
    configured, _code, _summary = gate.endpoint_decision(event, "   ")
    assert configured is False


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_configured_endpoint_runs(event):
    assert gate.endpoint_decision(event, "https://candidate.example.test") == (True, 0, "")


@pytest.mark.parametrize(
    ("event", "url", "expected_code", "expected_output", "summary_marker"),
    [
        ("pull_request", "", 0, "configured=false", "NOT release evidence"),
        ("workflow_dispatch", "", 1, "configured=false", "RAG_GOLDEN_BASE_URL"),
        ("workflow_dispatch", "https://candidate.example.test", 0, "configured=true", None),
    ],
)
def test_endpoint_cli_writes_github_output_and_summary(
    monkeypatch, tmp_path, capsys, event, url, expected_code, expected_output, summary_marker
):
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv(gate.ENDPOINT_ENV, url)

    assert gate.main(["endpoint", "--event-name", event]) == expected_code

    assert output.read_text(encoding="utf-8").strip() == expected_output
    stdout = capsys.readouterr().out
    if summary_marker is None:
        assert not summary.exists()
    else:
        assert summary_marker in summary.read_text(encoding="utf-8")
    if expected_code:
        assert "::error" in stdout
    elif not url:
        assert "::notice" in stdout and "NOT release evidence" in stdout


# --- manifest policy ---------------------------------------------------------


def test_complete_release_manifest_is_accepted(release_manifest, case_ids):
    assert gate.release_manifest_problems(release_manifest, case_ids) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("release_eligible", False, "release_eligible"),
        ("release_eligible", None, "release_eligible"),
        ("release_eligible", "true", "release_eligible"),
        ("complete", False, "complete"),
        ("selected_case_ids", ["AC-001"], "partial case selection"),
        ("failed_case_ids", ["AC-001"], "failed cases"),
        ("candidate_fingerprint_stable", False, "fingerprint"),
        ("result_count", 1, "result_count"),
    ],
)
def test_non_release_manifest_is_rejected(release_manifest, case_ids, field, value, reason):
    release_manifest[field] = value
    problems = gate.release_manifest_problems(release_manifest, case_ids)
    assert any(reason in problem for problem in problems), problems


def test_missing_gate_fields_are_rejected(case_ids):
    problems = gate.release_manifest_problems({}, case_ids)
    assert any("release_eligible" in problem for problem in problems)
    assert any("complete" in problem for problem in problems)


def test_manifest_not_covering_matrix_is_rejected(release_manifest, case_ids):
    release_manifest["result_count"] = release_manifest["expected_result_count"] = 1
    problems = gate.release_manifest_problems(release_manifest, case_ids)
    assert any("does not cover the matrix" in problem for problem in problems)


def test_manifest_cli(tmp_path, release_manifest, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(release_manifest), encoding="utf-8")
    assert gate.main(["manifest", "--manifest", str(path)]) == 0

    release_manifest["selected_case_ids"] = ["AC-001"]
    release_manifest["complete"] = release_manifest["release_eligible"] = False
    path.write_text(json.dumps(release_manifest), encoding="utf-8")
    assert gate.main(["manifest", "--manifest", str(path)]) == 2
    assert "NOT release evidence" in capsys.readouterr().err

    assert gate.main(["manifest", "--manifest", str(tmp_path / "missing.json")]) == 2


def test_manifest_cli_rejects_subset_manifest_with_all_flags_true(tmp_path, release_manifest, capsys):
    release_manifest["result_count"] = release_manifest["expected_result_count"] = 1
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(release_manifest), encoding="utf-8")

    assert gate.main(["manifest", "--manifest", str(path)]) == 2
    assert "does not cover the matrix" in capsys.readouterr().err


def test_manifest_cli_matrix_override(tmp_path, release_manifest, case_ids):
    matrix = tmp_path / "matrix.csv"
    lines = MATRIX.read_text(encoding="utf-8").splitlines(keepends=True)
    matrix.write_text("".join(lines[:2]), encoding="utf-8")
    release_manifest["result_count"] = release_manifest["expected_result_count"] = 1
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(release_manifest), encoding="utf-8")

    assert gate.main(["manifest", "--manifest", str(path), "--matrix", str(matrix)]) == 0
    assert gate.main(["manifest", "--manifest", str(path)]) == 2


@pytest.mark.parametrize("field", ["result_count", "expected_result_count"])
def test_bool_counts_are_rejected(release_manifest, field):
    release_manifest["result_count"] = release_manifest["expected_result_count"] = 1
    release_manifest[field] = True
    problems = gate.release_manifest_problems(release_manifest)
    assert any("result_count" in problem for problem in problems), problems
    release_manifest["result_count"] = release_manifest["expected_result_count"] = True
    assert gate.release_manifest_problems(release_manifest)


@pytest.mark.parametrize("payload", [[], [1, 2], "manifest", None, 3])
def test_non_object_manifest_is_rejected(payload):
    assert gate.release_manifest_problems(payload) == ["manifest is not a JSON object"]


# --- the gate is enforced by verify / evaluate -------------------------------


def _write_replay(directory: Path, manifest: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "results.jsonl").write_text("", encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("release_eligible", False),
        ("complete", False),
        ("selected_case_ids", ["AC-001"]),
    ],
)
def test_verify_rejects_non_release_manifest(
    monkeypatch, tmp_path, capsys, release_manifest, field, value
):
    release_manifest[field] = value
    replay = _write_replay(tmp_path / "replay", release_manifest)
    # Schema and result loading are covered elsewhere; isolate the release gate.
    monkeypatch.setattr(verify_golden_replay, "_validate_json", lambda path, _schema: json.loads(path.read_text()))
    monkeypatch.setattr(verify_golden_replay, "load_results", lambda _path, _schema: [])

    assert verify_golden_replay.main(["--replay-dir", str(replay)]) == 2
    assert "NOT release evidence" in capsys.readouterr().err


def test_evaluate_release_mode_rejects_subset_manifest(monkeypatch, tmp_path, capsys, release_manifest):
    release_manifest["selected_case_ids"] = ["AC-001"]
    release_manifest["complete"] = release_manifest["release_eligible"] = False
    replay = _write_replay(tmp_path / "replay", release_manifest)
    monkeypatch.setattr(evaluate_golden_matrix, "load_results", lambda _path, _schema: [])

    exit_code = evaluate_golden_matrix.main(
        [
            "--results", str(replay / "results.jsonl"),
            "--manifest", str(replay / "manifest.json"),
            "--output-dir", str(tmp_path / "report"),
            "--release",
        ]
    )

    assert exit_code == 2
    assert "NOT release evidence" in capsys.readouterr().err
    assert not (tmp_path / "report").exists()


@pytest.mark.parametrize("payload", [[], ["not", "an", "object"]])
def test_evaluate_release_mode_fails_closed_on_non_object_manifest(monkeypatch, tmp_path, capsys, payload):
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (replay / "results.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(evaluate_golden_matrix, "load_results", lambda _path, _schema: [])

    exit_code = evaluate_golden_matrix.main(
        [
            "--results", str(replay / "results.jsonl"),
            "--manifest", str(replay / "manifest.json"),
            "--output-dir", str(tmp_path / "report"),
            "--release",
        ]
    )

    assert exit_code == 2
    assert "manifest is not a JSON object" in capsys.readouterr().err
    assert not (tmp_path / "report").exists()


# --- workflow wiring ---------------------------------------------------------


def test_workflow_wires_the_release_gate():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers and "workflow_dispatch" in triggers

    steps = workflow["jobs"]["real-candidate-gate"]["steps"]
    assert all(not step.get("continue-on-error") for step in steps)
    detect = next(step for step in steps if step.get("id") == "candidate")
    assert "check_golden_release_gate.py endpoint" in detect["run"]
    assert "github.event_name" in detect["env"]["EVENT_NAME"]
    assert "if" not in detect, "endpoint detection must always run so dispatch runs can fail"

    by_name = {step.get("name"): step for step in steps}
    assert "verify_golden_replay.py" in by_name["Verify replay provenance"]["run"]
    assert "--release" in by_name["Evaluate four release axes"]["run"]
    evidence = by_name["Record release evidence"]
    assert "always()" not in evidence.get("if", "")
    assert "github.event_name != 'pull_request'" in evidence["if"]
    pr_result = by_name["Record PR run result"]
    assert "github.event_name == 'pull_request'" in pr_result["if"]
    assert "NOT release evidence" in pr_result["run"]
    assert steps.index(evidence) > steps.index(by_name["Evaluate four release axes"])
