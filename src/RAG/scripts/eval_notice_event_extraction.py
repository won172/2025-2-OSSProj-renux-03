"""공지 일정 추출 골드셋(`tests/fixtures/notice_event_gold.csv`)으로 현행 추출기를 측정한다.

공지 → 캘린더 이벤트 기능의 **기준선(baseline)** 측정용이다. 현행 추출기
`_extract_notice_apply_deadline(title, body, published_date)`는 날짜 하나(마감일)만
돌려주므로, 골드의 `end_date`와 비교한다. 시작일·이벤트 종류·대상은 표현하지 못한다.

다회차 공지(`multi_event=true`: 회차가 여러 개라 하나의 기간으로 표현할 수 없는 공지)는 골드 날짜를
공란으로 두므로 핵심 지표(정확도·precision·recall·확신 오답)에서 제외하고 따로 보고한다.

주의: 골드 라벨은 `label_source=auto_draft_claude`, `human_verified=false`인 **자동 초안**이다.
사람이 확인하기 전까지 이 수치를 품질 판정으로 쓰지 않는다. DB 없이 CSV만 읽는다.

본문 입력은 두 가지다.
  - 기본(excerpt): CSV의 `body_excerpt`(근거·기간 라벨 주변 발췌). 공개 저장소에 원문 전체를 싣지 않기 위함.
  - `--db <path>`: 승인된 DB **사본**을 읽기 전용(sqlite `mode=ro`)으로 열어 `notice_id`로 원문 본문을
    가져와 ingest와 같은 정제(strip_html + normalize_whitespace)를 거쳐 사용한다. 운영 DB에 쓰지 않는다.
    이 모드에서는 발췌 기준 예측과 다른 행 수(`excerpt_vs_full_mismatches`)도 함께 보고한다.

사용:
    python scripts/eval_notice_event_extraction.py
    python scripts/eval_notice_event_extraction.py --db /path/to/readonly-copy/rag_database.db
    python scripts/eval_notice_event_extraction.py --json-out /tmp/notice_event_baseline.json
    python scripts/eval_notice_event_extraction.py --show-errors
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipelines.ingest import _extract_notice_apply_deadline  # noqa: E402
from src.utils.preprocess import normalize_whitespace, strip_html  # noqa: E402

GOLD_PATH = ROOT / "tests" / "fixtures" / "notice_event_gold.csv"


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def load_gold(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_full_bodies(db_path: Path, notice_ids: List[str]) -> Dict[str, str]:
    """DB 사본에서 원문 본문을 읽기 전용으로 가져와 ingest와 같은 방식으로 정제한다."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        bodies: Dict[str, str] = {}
        for notice_id in notice_ids:
            found = conn.execute("SELECT content FROM notices WHERE id = ?", (int(notice_id),)).fetchone()
            if found is None:
                raise SystemExit(f"notice_id {notice_id} not found in {db_path}")
            bodies[notice_id] = normalize_whitespace(strip_html(found[0] or ""))
        return bodies
    finally:
        conn.close()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def evaluate(
    rows: List[Dict[str, str]],
    gold_path: Path = GOLD_PATH,
    full_bodies: Dict[str, str] | None = None,
) -> Dict[str, object]:
    per_row = []
    mismatches = []
    for row in rows:
        excerpt_pred = _extract_notice_apply_deadline(
            row["title"], row["body_excerpt"], row["published_date"] or None
        )
        predicted = excerpt_pred
        if full_bodies is not None:
            predicted = _extract_notice_apply_deadline(
                row["title"], full_bodies[row["notice_id"]], row["published_date"] or None
            )
            if predicted != excerpt_pred:
                mismatches.append(
                    {"source_key": row["source_key"], "full_body": predicted, "excerpt": excerpt_pred}
                )
        gold_end = row["end_date"].strip() or None
        multi_event = row.get("multi_event", "false") == "true"
        if multi_event:
            outcome = "multi_event_predicted" if predicted else "multi_event_blank"
        elif predicted is None and gold_end is None:
            outcome = "correct_blank"
        elif predicted is None:
            outcome = "missed"
        elif predicted == gold_end:
            outcome = "correct_date"
        elif gold_end is None:
            outcome = "spurious_date"  # 골드에 날짜가 없는데 날짜를 냄
        else:
            outcome = "wrong_date"  # 골드와 다른 날짜를 냄
        per_row.append(
            {
                "source_key": row["source_key"],
                "title": row["title"],
                "event_kind": row["event_kind"],
                "multi_event": multi_event,
                "year_inferred": row.get("year_inferred", "false") == "true",
                "content_location": row["content_location"],
                "gold_start_date": row["start_date"] or None,
                "gold_end_date": gold_end,
                "predicted": predicted,
                "outcome": outcome,
                "human_review_priority": row.get("human_review_priority", ""),
            }
        )

    multi_rows = [r for r in per_row if r["multi_event"]]
    scored = [r for r in per_row if not r["multi_event"]]
    n = len(scored)
    outcomes = Counter(r["outcome"] for r in scored)
    predicted_n = sum(1 for r in scored if r["predicted"] is not None)
    gold_end_n = sum(1 for r in scored if r["gold_end_date"] is not None)
    correct_date = outcomes["correct_date"]
    confidently_wrong = outcomes["wrong_date"] + outcomes["spurious_date"]
    with_start = [r for r in scored if r["gold_start_date"]]
    with_period = [r for r in with_start if r["gold_start_date"] != r["gold_end_date"]]
    locations = Counter(r["content_location"] for r in per_row)

    def breakdown(key: str) -> Dict[str, Dict[str, object]]:
        groups: Dict[str, List[dict]] = defaultdict(list)
        for r in scored:
            groups[r[key]].append(r)
        result = {}
        for name, items in sorted(groups.items()):
            counts = Counter(i["outcome"] for i in items)
            with_gold = sum(1 for i in items if i["gold_end_date"])
            result[name] = {
                "rows": len(items),
                "gold_end_date_rows": with_gold,
                "recall": _ratio(counts["correct_date"], with_gold),
                "outcomes": dict(sorted(counts.items())),
            }
        return result

    return {
        "gold_file": _display_path(gold_path),
        "label_status": "auto_draft_claude / human_verified=false — 사람 검증 전 초안",
        "extractor": "src.pipelines.ingest._extract_notice_apply_deadline(title, body, published_date)",
        "body_source": "full_body_db_readonly" if full_bodies is not None else "body_excerpt",
        "excerpt_vs_full_mismatches": mismatches if full_bodies is not None else None,
        "rows_total": len(per_row),
        "rows": n,
        "rows_with_gold_end_date": gold_end_n,
        "rows_with_prediction": predicted_n,
        "outcomes": dict(sorted(outcomes.items())),
        "metrics": {
            # 날짜 없음=날짜 없음도 일치로 센다.
            "exact_match_accuracy_all_rows": _ratio(
                correct_date + outcomes["correct_blank"], n
            ),
            "precision_when_predicting": _ratio(correct_date, predicted_n),
            "recall_over_gold_end_date": _ratio(correct_date, gold_end_n),
            "confidently_wrong_count": confidently_wrong,
            "confidently_wrong_rate_of_predictions": _ratio(confidently_wrong, predicted_n),
            "confidently_wrong_rate_of_all_rows": _ratio(confidently_wrong, n),
        },
        "start_date_unrepresentable": {
            # 추출기는 시작일 출력이 없다.
            "rows_with_gold_start_date": len(with_start),
            "rows_with_period_start_ne_end": len(with_period),
            "share_of_all_rows": _ratio(len(with_start), n),
        },
        "multi_event": {
            # 핵심 지표에서 제외. 추출기가 낸 날짜는 회차 중 하나일 뿐이라 정오를 매기지 않는다.
            "rows": len(multi_rows),
            "rows_with_prediction": sum(1 for r in multi_rows if r["predicted"]),
            "items": [
                {k: r[k] for k in ("source_key", "title", "event_kind", "predicted")}
                for r in multi_rows
            ],
        },
        "year_inferred_rows": sum(1 for r in scored if r["year_inferred"]),
        "content_location": {
            "counts": dict(sorted(locations.items())),
            "attachment_only_share": _ratio(locations["attachment_only"], len(per_row)),
            "none_share": _ratio(locations["none"], len(per_row)),
        },
        "by_event_kind": breakdown("event_kind"),
        "by_content_location": breakdown("content_location"),
        "errors": [r for r in per_row if r["outcome"] in {"missed", "wrong_date", "spurious_date"}],
    }


def print_summary(report: Dict[str, object], show_errors: bool) -> None:
    m = report["metrics"]
    s = report["start_date_unrepresentable"]
    c = report["content_location"]
    mev = report["multi_event"]
    print(f"골드: {report['gold_file']} ({report['rows_total']}행, {report['label_status']})")
    print(
        f"다회차(multi_event) {mev['rows']}행은 핵심 지표에서 제외(그중 추출기가 날짜를 낸 행 {mev['rows_with_prediction']}) "
        f"→ 채점 대상 {report['rows']}행"
    )
    print(f"추출기: {report['extractor']} / 본문 입력: {report['body_source']}")
    if report["excerpt_vs_full_mismatches"] is not None:
        print(f"발췌 vs 전체 본문 예측 불일치: {len(report['excerpt_vs_full_mismatches'])}행 {report['excerpt_vs_full_mismatches']}")
    print(f"gold end_date 있는 행: {report['rows_with_gold_end_date']}, 예측 낸 행: {report['rows_with_prediction']}")
    print(f"결과 분포: {report['outcomes']}")
    print(f"- 마감일 exact-match 정확도(전체, 공란=공란 포함): {m['exact_match_accuracy_all_rows']}")
    print(f"- 예측 시 precision: {m['precision_when_predicting']}")
    print(f"- gold end_date 대비 recall: {m['recall_over_gold_end_date']}")
    print(
        f"- 확신 오답(다른 날짜 또는 골드 공란인데 날짜): {m['confidently_wrong_count']}건, "
        f"예측 대비 {m['confidently_wrong_rate_of_predictions']}, 전체 대비 {m['confidently_wrong_rate_of_all_rows']}"
    )
    print(
        f"- 추출기가 표현 못 하는 start_date: {s['rows_with_gold_start_date']}행 "
        f"(start≠end 기간형 {s['rows_with_period_start_ne_end']}행)"
    )
    print(
        f"- content_location: {c['counts']} → attachment_only {c['attachment_only_share']}, none {c['none_share']}"
    )
    print("event_kind별:")
    for name, info in report["by_event_kind"].items():
        print(f"  {name}: {info}")
    if show_errors:
        print("오류 행:")
        for r in report["errors"]:
            print(
                f"  [{r['outcome']}] {r['source_key']} gold={r['gold_end_date']} pred={r['predicted']} "
                f"({r['event_kind']}/{r['content_location']}) {r['title'][:50]}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, default=GOLD_PATH)
    parser.add_argument("--json-out", type=Path, help="JSON 결과를 저장할 경로(없으면 stdout에 출력)")
    parser.add_argument("--show-errors", action="store_true")
    parser.add_argument(
        "--db",
        type=Path,
        help="원문 본문을 읽을 RAG DB 사본 경로(읽기 전용으로 연다). 없으면 body_excerpt 사용",
    )
    args = parser.parse_args()

    rows = load_gold(args.gold)
    full_bodies = load_full_bodies(args.db, [r["notice_id"] for r in rows]) if args.db else None
    report = evaluate(rows, args.gold, full_bodies)
    print_summary(report, args.show_errors)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.write_text(payload + "\n", encoding="utf-8")
        print(f"JSON 저장: {args.json_out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
