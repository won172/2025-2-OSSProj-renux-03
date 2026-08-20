"""Versioned structural contract for the real RAG release matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TAXONOMY_VERSION = "student-domain-app-intent-v1"
DOMAINS = (
    "academic-calendar",
    "registration",
    "graduation",
    "scholarship",
    "academic-status",
    "campus-life",
    "dining",
    "housing",
    "career",
    "international",
    "people-services",
    "student-community",
)
APP_INTENTS = {"notices", "rules", "schedule", "staff", "courses", "meals", "unknown"}
DATASETS = APP_INTENTS - {"unknown"}
CASE_TYPES = {
    "basic",
    "ambiguous",
    "date",
    "department_or_facility",
    "typo",
    "cross_domain",
    "not_answerable",
    "wise_boundary",
    # 아래 둘은 모든 도메인에 요구하지 않는다(REQUIRED_CASE_TYPES 참고).
    "historical_info",
    "nonexistent_info",
}
# 도메인마다 반드시 있어야 하는 유형. 새 두 유형은 코퍼스가 뒷받침할 때만
# 넣는다 — 예를 들어 meals 코퍼스는 2주치뿐이라 dining의 과거 정보 문항은
# 정답을 만들 수 없고, staff 명부에는 시점이 없다. 없는 정답을 지어내 채우면
# 문항 수만 늘고 지표는 거짓이 된다.
REQUIRED_CASE_TYPES = CASE_TYPES - {"historical_info", "nonexistent_info"}
REQUIRED_COLUMNS = (
    "id",
    "question",
    "domain",
    "case_type",
    "expected_product_intent",
    "expected_app_intent",
    "expected_datasets",
    "allowed_campuses",
    "required_campuses",
    "campus_requirement_mode",
    "expected_source_types",
    "required_keywords",
    "forbidden_claims",
    "answerability",
    "followup_policy",
    "source_requirement",
    "date_requirement",
    "citation_requirement",
    "privacy_requirement",
    "forbidden_pii_types",
    "refusal_reason",
    "refusal_markers",
    "clarification_fields",
)
# WISE is retained in source metadata for quarantine checks, but it is not an
# answerable product campus and therefore cannot be an expected/allowed source.
ALLOWED_CAMPUSES = {"seoul", "bmc"}
ANSWERABILITY = {"answerable", "needs_clarification", "not_answerable"}
FOLLOWUP_POLICIES = {"grounded_next_steps", "clarify", "official_contact", "none"}
SOURCE_REQUIREMENTS = {"trusted_official", "official_contact", "no_source_required"}
DATE_REQUIREMENTS = {"none", "published_or_effective", "current_or_effective", "campus_comparison_dates"}
CITATION_REQUIREMENTS = {"claim_source_links", "official_contact_source", "none"}
PRIVACY_REQUIREMENTS = {"none", "no_sensitive_disclosure"}
MIN_QUESTIONS = 160
MIN_PER_DOMAIN = 10
# historical_info 문항이 실제로 과거를 지목하는지 확인할 때 쓴다.
_EXPLICIT_YEAR_RE = re.compile(r"20\d{2}")


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    domain: str
    case_types: tuple[str, ...]
    expected_product_intents: tuple[str, ...]
    expected_app_intents: tuple[str, ...]
    expected_datasets: tuple[str, ...]
    allowed_campuses: tuple[str, ...]
    required_campuses: tuple[str, ...]
    campus_requirement_mode: str
    expected_source_types: tuple[str, ...]
    required_keywords: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    answerability: str
    followup_policy: str
    source_requirement: str
    date_requirement: str
    citation_requirement: str
    privacy_requirement: str
    forbidden_pii_types: tuple[str, ...]
    refusal_reason: str
    refusal_markers: tuple[str, ...]
    clarification_fields: tuple[str, ...]


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(";") if part.strip())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_taxonomy(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != TAXONOMY_VERSION:
        raise ValueError(f"taxonomy version must be {TAXONOMY_VERSION!r}")
    if set(data.get("app_intents", [])) != APP_INTENTS:
        raise ValueError("taxonomy app_intents do not match the runtime seven-intent contract")
    mappings = data.get("product_domain_to_app_intents")
    if not isinstance(mappings, dict) or set(mappings) != set(DOMAINS):
        raise ValueError("taxonomy must map every product domain")
    return data


def load_matrix(path: Path) -> list[GoldenCase]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")
        return [
            GoldenCase(
                id=(row["id"] or "").strip(),
                question=(row["question"] or "").strip(),
                domain=(row["domain"] or "").strip(),
                case_types=_split(row["case_type"]),
                expected_product_intents=_split(row["expected_product_intent"]),
                expected_app_intents=_split(row["expected_app_intent"]),
                expected_datasets=_split(row["expected_datasets"]),
                allowed_campuses=_split(row["allowed_campuses"]),
                required_campuses=_split(row["required_campuses"]),
                campus_requirement_mode=(row["campus_requirement_mode"] or "").strip(),
                expected_source_types=_split(row["expected_source_types"]),
                required_keywords=_split(row["required_keywords"]),
                forbidden_claims=_split(row["forbidden_claims"]),
                answerability=(row["answerability"] or "").strip(),
                followup_policy=(row["followup_policy"] or "").strip(),
                source_requirement=(row["source_requirement"] or "").strip(),
                date_requirement=(row["date_requirement"] or "").strip(),
                citation_requirement=(row["citation_requirement"] or "").strip(),
                privacy_requirement=(row["privacy_requirement"] or "").strip(),
                forbidden_pii_types=_split(row["forbidden_pii_types"]),
                refusal_reason=(row["refusal_reason"] or "").strip(),
                refusal_markers=_split(row["refusal_markers"]),
                clarification_fields=_split(row["clarification_fields"]),
            )
            for row in reader
        ]


def _normalized_question(question: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", question).lower()


def _template_question(question: str) -> str:
    collapsed_numbers = re.sub(r"\d+(?:[./:-]\d+)*", "#", question)
    return re.sub(r"[^#a-zA-Z가-힣]", "", collapsed_numbers).lower()


def validate_matrix(
    cases: Iterable[GoldenCase],
    taxonomy: dict,
    *,
    min_questions: int = MIN_QUESTIONS,
    min_per_domain: int = MIN_PER_DOMAIN,
) -> list[str]:
    rows = list(cases)
    errors: list[str] = []
    if len(rows) < min_questions:
        errors.append(f"question count {len(rows)} is below {min_questions}")

    domain_counts = Counter(row.domain for row in rows)
    for unknown in sorted(set(domain_counts) - set(DOMAINS)):
        errors.append(f"unknown domain: {unknown}")
    type_counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        type_counts[row.domain].update(row.case_types)
    for domain in DOMAINS:
        count = domain_counts.get(domain, 0)
        if count < min_per_domain:
            errors.append(f"domain {domain} has {count} questions; requires {min_per_domain}")
        for case_type in sorted(REQUIRED_CASE_TYPES):
            if type_counts[domain][case_type] < 1:
                errors.append(f"domain {domain} has no {case_type} case")

    ids: set[str] = set()
    questions: dict[str, str] = {}
    templates: dict[str, str] = {}
    mappings = taxonomy["product_domain_to_app_intents"]
    for index, row in enumerate(rows, start=2):
        label = row.id or f"CSV row {index}"
        if not row.id:
            errors.append(f"CSV row {index}: id is empty")
        elif row.id in ids:
            errors.append(f"{label}: duplicate id")
        ids.add(row.id)

        normalized = _normalized_question(row.question)
        existing = questions.get(normalized)
        if not normalized:
            errors.append(f"{label}: question is empty")
        elif existing:
            errors.append(f"{label}: duplicate question with {existing}")
        else:
            questions[normalized] = label
        template = _template_question(row.question)
        if template in templates and not existing:
            errors.append(f"{label}: numeric-only question template duplicates {templates[template]}")
        elif template:
            templates[template] = label

        invalid_types = sorted(set(row.case_types) - CASE_TYPES)
        if not row.case_types or invalid_types:
            errors.append(f"{label}: invalid case_type values {invalid_types or list(row.case_types)}")
        if not row.expected_product_intents:
            errors.append(f"{label}: expected_product_intent is empty")
        invalid_app = sorted(set(row.expected_app_intents) - APP_INTENTS)
        if not row.expected_app_intents or invalid_app:
            errors.append(f"{label}: invalid expected_app_intent values {invalid_app or list(row.expected_app_intents)}")
        allowed_for_domain = set(mappings.get(row.domain, []))
        if not set(row.expected_app_intents).issubset(allowed_for_domain | {"unknown"}):
            errors.append(f"{label}: app intent is not mapped from product domain {row.domain}")
        invalid_datasets = sorted(set(row.expected_datasets) - DATASETS)
        if invalid_datasets:
            errors.append(f"{label}: invalid expected_datasets {invalid_datasets}")

        invalid_allowed = sorted(set(row.allowed_campuses) - ALLOWED_CAMPUSES)
        invalid_required = sorted(set(row.required_campuses) - ALLOWED_CAMPUSES)
        if not row.allowed_campuses or invalid_allowed:
            errors.append(f"{label}: invalid allowed_campuses {invalid_allowed or list(row.allowed_campuses)}")
        if not row.required_campuses or invalid_required:
            errors.append(f"{label}: invalid required_campuses {invalid_required or list(row.required_campuses)}")
        if not set(row.required_campuses).issubset(row.allowed_campuses):
            errors.append(f"{label}: required_campuses must be a subset of allowed_campuses")
        if row.campus_requirement_mode not in {"any", "all"}:
            errors.append(f"{label}: campus_requirement_mode must be any or all")

        if row.answerability not in ANSWERABILITY:
            errors.append(f"{label}: invalid answerability {row.answerability!r}")
        if row.followup_policy not in FOLLOWUP_POLICIES:
            errors.append(f"{label}: invalid followup_policy {row.followup_policy!r}")
        if row.source_requirement not in SOURCE_REQUIREMENTS:
            errors.append(f"{label}: invalid source_requirement {row.source_requirement!r}")
        if row.date_requirement not in DATE_REQUIREMENTS:
            errors.append(f"{label}: invalid date_requirement {row.date_requirement!r}")
        if row.citation_requirement not in CITATION_REQUIREMENTS:
            errors.append(f"{label}: invalid citation_requirement {row.citation_requirement!r}")
        if row.privacy_requirement not in PRIVACY_REQUIREMENTS:
            errors.append(f"{label}: invalid privacy_requirement {row.privacy_requirement!r}")

        if "ambiguous" in row.case_types:
            if row.answerability != "needs_clarification" or not row.clarification_fields:
                errors.append(f"{label}: ambiguous case needs clarification fields and answerability")
        if "not_answerable" in row.case_types:
            if row.answerability != "not_answerable" or not row.refusal_reason or not row.refusal_markers:
                errors.append(f"{label}: not-answerable case needs a row-specific refusal contract")
            if row.privacy_requirement == "no_sensitive_disclosure" and not row.forbidden_pii_types:
                errors.append(f"{label}: privacy refusal requires forbidden_pii_types")
        if "wise_boundary" in row.case_types:
            if row.answerability != "not_answerable":
                errors.append(f"{label}: WISE boundary case must be out_of_scope/not_answerable")
            if row.expected_app_intents != ("unknown",):
                errors.append(f"{label}: WISE boundary case must resolve to unknown")
            if row.expected_datasets or row.expected_source_types:
                errors.append(f"{label}: WISE boundary case must not require retrieval sources")
            if row.source_requirement != "no_source_required" or row.followup_policy != "none":
                errors.append(f"{label}: WISE boundary case must not require sources or follow-ups")
            if row.refusal_reason != "campus_out_of_scope" or not row.refusal_markers:
                errors.append(f"{label}: WISE boundary case needs the campus_out_of_scope refusal contract")
            if "wise" in row.allowed_campuses or "wise" in row.required_campuses:
                errors.append(f"{label}: WISE must not be an allowed or required answer campus")
        if "cross_domain" in row.case_types and len(row.required_keywords) < 2:
            errors.append(f"{label}: cross-domain case must declare every answer axis")
        if "historical_info" in row.case_types:
            # 과거를 묻는다는 사실이 질문 안에 드러나야 한다. 연도가 없으면
            # `extract_explicit_years`가 최신성 가중을 끄지 않아 이 문항은
            # 검증하려던 경로를 아예 밟지 않는다.
            if not _EXPLICIT_YEAR_RE.search(row.question):
                errors.append(f"{label}: historical case must name an explicit year in the question")
            if row.answerability != "answerable":
                errors.append(f"{label}: historical case must be answerable from archived evidence")
            if not any(_EXPLICIT_YEAR_RE.search(keyword) for keyword in row.required_keywords):
                errors.append(f"{label}: historical case must require the year as an answer keyword")
            if not any("현재" in claim or "지금" in claim for claim in row.forbidden_claims):
                errors.append(f"{label}: historical case must forbid presenting the past as current")
        if "nonexistent_info" in row.case_types:
            # 자료 부재는 권한·개인정보 거절과 다르다. 같은 not_answerable이지만
            # 처방이 정반대다 — 앞은 사람이 자료를 채워야 하고, 뒤는 채우면 안 된다.
            if row.answerability != "not_answerable":
                errors.append(f"{label}: absent-data case must be not_answerable")
            if row.refusal_reason != "data_absent_in_corpus" or not row.refusal_markers:
                errors.append(f"{label}: absent-data case needs the data_absent_in_corpus contract")
            if row.required_keywords:
                errors.append(
                    f"{label}: absent-data case must not declare answer keywords "
                    "(there is nothing to answer, so a keyword contract would pass a fabricated answer)"
                )
            if not row.expected_datasets:
                errors.append(
                    f"{label}: absent-data case must declare where it was searched, "
                    "so retrieval runs and finding nothing is what gets verified"
                )
        if row.answerability == "answerable":
            if not row.expected_datasets or not row.expected_source_types:
                errors.append(f"{label}: answerable case needs datasets and source types")
            if row.source_requirement != "trusted_official" or row.citation_requirement != "claim_source_links":
                errors.append(f"{label}: answerable case needs official sources and claim links")
        if "wise" not in " ".join(row.forbidden_claims).lower():
            errors.append(f"{label}: forbidden_claims must include a WISE guard")

    bmc_only = {"DN-003", "HS-009", "PS-005"}
    for row in rows:
        if row.id in bmc_only and (
            row.allowed_campuses != ("bmc",)
            or row.required_campuses != ("bmc",)
            or row.campus_requirement_mode != "all"
        ):
            errors.append(f"{row.id}: BMC facility case must require BMC-only evidence")
    return errors


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate the release golden matrix")
    parser.add_argument("--matrix", type=Path, default=root / "tests" / "golden_matrix.csv")
    parser.add_argument("--taxonomy", type=Path, default=root / "tests" / "golden_taxonomy.v1.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        taxonomy = load_taxonomy(args.taxonomy)
        cases = load_matrix(args.matrix)
        errors = validate_matrix(cases, taxonomy)
    except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        errors = [str(exc)]
        cases = []
    summary = {
        "taxonomy_version": TAXONOMY_VERSION,
        "matrix_sha256": file_sha256(args.matrix) if args.matrix.exists() else None,
        "question_count": len(cases),
        "domain_counts": dict(sorted(Counter(row.domain for row in cases).items())),
        "errors": errors,
        "passed": not errors,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif errors:
        print("Golden matrix validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    else:
        print(f"Golden matrix valid: {len(cases)} real-run cases")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
