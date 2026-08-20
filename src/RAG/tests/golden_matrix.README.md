# Student-domain golden matrix

`golden_matrix.csv` is the release-quality contract for the 12 student domains
defined in `docs/audits/09-pm-student-adoption-plan.md`. It currently contains
14 distinct Korean questions per domain (168 total). The questions cover normal
requests, ambiguous wording, date expressions, department context, typos,
cross-domain requests, non-answerable requests, and campus-confusion attacks.

## Matrix format

The CSV columns are validated by `scripts/golden_matrix.py`. Multi-value fields
use `;` as the separator.

- `expected_intent`: one or more acceptable intent identifiers.
- `allowed_campuses`: campuses that may support this case (`seoul` or `bmc`).
  WISE is retained only as a quarantine label and is never an allowed answer
  campus. A result source labelled `shared` is accepted only when both Seoul
  and BMC are allowed.
- `expected_source_types`: at least one required source type for an answerable
  case.
- `required_keywords`: deterministic answer-completeness evidence. This is a
  keyword contract and is not presented as semantic correctness.
- `forbidden_claims`: exact unsupported claims that must never appear. Every
  row carries an explicit WISE guard.
- `answerability`: `answerable`, `needs_clarification`, or `not_answerable`.
- `followup_policy`: `grounded_next_steps`, `clarify`, `official_contact`, or
  `none`.

The validator also rejects missing domains, fewer than 160 total questions,
fewer than 10 questions in a domain, duplicate IDs/questions, and prompts copied
with only numbers or dates changed. The `wise_boundary` case type expects a
deterministic out-of-scope response with no retrieval source or follow-up.

```bash
python scripts/golden_matrix.py --json
```

## Four-axis result evaluation

Model outputs are JSONL rows conforming to `golden_result.schema.json`. The
evaluator writes per-question details plus domain-level JSON and Markdown
reports. The gate is intentionally strict: every declared result must be
present and pass intent, answer, source, and follow-up contracts; WISE evidence
in any product answer is always fatal.

```bash
python scripts/evaluate_golden_matrix.py \
  --results /path/to/model-results.jsonl \
  --output-dir /path/to/report-directory
```

The pytest fixture proves the scoring and failure behavior without calling
OpenAI. It is not evidence of real RAG quality. A release candidate must run the
actual 168 questions against the candidate model and retain the generated JSON
and Markdown reports.
