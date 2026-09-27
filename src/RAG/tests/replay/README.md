# Golden replay hold point

PR CI intentionally remains on hold until `latest/results.jsonl` and
`latest/manifest.json` are produced by `scripts/run_golden_matrix.py` from a
clean candidate checkout. Synthetic fixtures and partial `--case-id` runs are
not accepted as release evidence.

The two files are bound to the exact matrix, taxonomy, Git commit, model and
embedding configuration, data artifacts, and result bytes. After generation,
run both commands before promoting the candidate:

```bash
python scripts/verify_golden_replay.py --replay-dir tests/replay/latest
python scripts/evaluate_golden_matrix.py \
  --results tests/replay/latest/results.jsonl \
  --manifest tests/replay/latest/manifest.json \
  --output-dir tests/replay/latest/report \
  --release
```

## Release gate semantics

`.github/workflows/rag-golden-release.yml` delegates its decisions to
`scripts/check_golden_release_gate.py` (tested by
`tests/test_golden_release_gate.py`):

| Run | `RAG_GOLDEN_BASE_URL` | Result |
|---|---|---|
| `pull_request` | missing | Skipped, job green, summary says **NOT release evidence** |
| `workflow_dispatch` (release run) or any other event | missing | **Fails** |
| any | set | Full matrix runs; any failing step fails the job |

A manifest is release evidence only when `release_eligible` and `complete` are
`true`, `selected_case_ids` and `failed_case_ids` are empty, the candidate
fingerprint stayed stable, and `result_count` equals `expected_result_count`
and covers the whole matrix. `verify_golden_replay.py` always enforces this;
`evaluate_golden_matrix.py` enforces it with `--release` (the workflow passes
it). Without `--release` the evaluator can still score subset/troubleshooting
runs, but those reports are never release evidence. A green job counts as
release evidence only when its summary says "통과 — release evidence".
