"""골든 실행 두 개를 문항 단위로 대조한다.

`evaluate_golden_matrix.py`는 한 실행의 합격률을 낸다. 그런데 ablation에서
알아야 하는 것은 합격률의 차이가 아니라 **어느 문항이 뒤집혔는가**다. 평균은
상쇄된다 — 5건이 좋아지고 5건이 나빠져도 합계는 그대로이고, 그 상태를 "차이
없음"으로 읽으면 회귀를 그대로 배포하게 된다.

`config.py:83-90`에 남아 있는 사건이 이 도구가 필요한 이유다. 제목 가산을 끄는
변경이 한 지표에서는 오르고 다른 지표에서는 내렸는데, 실제로 무너진 것은
"2학기 개강일 알려줘" 한 문항이었다. 그런 문항은 축 합계로는 보이지 않는다.

사용:
    python scripts/compare_golden_runs.py --baseline <report_dir> --candidate <report_dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AXES = ("intent", "answer", "source", "followup")


def _load(report_dir: Path) -> tuple[dict, dict]:
    summary_path = report_dir / "golden_evaluation_summary.json"
    details_path = report_dir / "golden_evaluation_details.json"
    for path in (summary_path, details_path):
        if not path.exists():
            raise FileNotFoundError(
                f"평가 결과가 없습니다: {path}\n"
                "먼저 scripts/evaluate_golden_matrix.py 로 보고서를 만드세요."
            )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    details = json.loads(details_path.read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in details if isinstance(row, dict) and row.get("id")}
    return summary, by_id


def _axis_pass(row: dict[str, Any], axis: str) -> bool:
    return bool((row.get("axes") or {}).get(axis, {}).get("passed"))


def _axis_reason(row: dict[str, Any], axis: str) -> str:
    node = (row.get("axes") or {}).get(axis) or {}
    for key in ("reason", "detail", "message", "failures"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:110]
        if isinstance(value, list) and value:
            return "; ".join(str(item) for item in value)[:110]
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, required=True, help="기준선 보고서 디렉터리")
    parser.add_argument("--candidate", type=Path, required=True, help="비교할 보고서 디렉터리")
    parser.add_argument("--show", type=int, default=12, help="뒤집힌 문항을 몇 건까지 출력할지")
    args = parser.parse_args()

    base_summary, base_rows = _load(args.baseline)
    cand_summary, cand_rows = _load(args.candidate)

    shared = sorted(set(base_rows) & set(cand_rows))
    only_base = sorted(set(base_rows) - set(cand_rows))
    only_cand = sorted(set(cand_rows) - set(base_rows))

    print("=" * 74)
    print(f"골든 실행 비교 · 공통 문항 {len(shared)}건")
    print("=" * 74)
    if only_base or only_cand:
        # 한쪽에만 있는 문항을 평균에 섞으면 비교가 아니라 서로 다른 시험이 된다.
        print(f"  ※ 기준선에만 {len(only_base)}건, 후보에만 {len(only_cand)}건 — 비교에서 제외")

    print(f"\n  {'axis':<10} {'baseline':>10} {'candidate':>10} {'delta':>9}")
    for axis in AXES:
        base_pass = sum(_axis_pass(base_rows[i], axis) for i in shared)
        cand_pass = sum(_axis_pass(cand_rows[i], axis) for i in shared)
        delta = cand_pass - base_pass
        mark = "+" if delta > 0 else ""
        print(
            f"  {axis:<10} {base_pass / len(shared):>9.1%} {cand_pass / len(shared):>10.1%} "
            f"{mark}{delta:>8d}건"
        )

    base_all = sum(bool(base_rows[i].get("all_axes_passed")) for i in shared)
    cand_all = sum(bool(cand_rows[i].get("all_axes_passed")) for i in shared)
    delta_all = cand_all - base_all
    mark = "+" if delta_all > 0 else ""
    print(
        f"  {'전체통과':<8} {base_all / len(shared):>9.1%} {cand_all / len(shared):>10.1%} "
        f"{mark}{delta_all:>8d}건"
    )

    # 문항 단위 뒤집힘. 축별로 나눠 봐야 무엇이 달라졌는지 알 수 있다.
    gained: dict[str, list[str]] = defaultdict(list)
    lost: dict[str, list[str]] = defaultdict(list)
    for case_id in shared:
        for axis in AXES:
            before = _axis_pass(base_rows[case_id], axis)
            after = _axis_pass(cand_rows[case_id], axis)
            if after and not before:
                gained[axis].append(case_id)
            elif before and not after:
                lost[axis].append(case_id)

    total_lost = sum(len(v) for v in lost.values())
    total_gained = sum(len(v) for v in gained.values())
    print(f"\n  축 단위 뒤집힘: 개선 {total_gained} · 악화 {total_lost}")

    if total_lost:
        print(f"\n  ▼ 악화 (배포 전에 확인할 것)")
        shown = 0
        for axis in AXES:
            for case_id in lost[axis]:
                if shown >= args.show:
                    break
                domain = cand_rows[case_id].get("domain", "")
                reason = _axis_reason(cand_rows[case_id], axis)
                print(f"    {case_id:<8} {axis:<9} {domain:<18} {reason}")
                shown += 1
        remaining = total_lost - shown
        if remaining > 0:
            print(f"    … 그 외 {remaining}건 (--show 로 더 보기)")

    if total_gained:
        print(f"\n  ▲ 개선")
        shown = 0
        for axis in AXES:
            for case_id in gained[axis]:
                if shown >= args.show:
                    break
                domain = cand_rows[case_id].get("domain", "")
                print(f"    {case_id:<8} {axis:<9} {domain}")
                shown += 1
        remaining = total_gained - shown
        if remaining > 0:
            print(f"    … 그 외 {remaining}건")

    # 캠퍼스 오답은 골든 계약에서 치명 등급이라 따로 센다.
    base_wrong = sum(bool(base_rows[i].get("wrong_campus")) for i in shared)
    cand_wrong = sum(bool(cand_rows[i].get("wrong_campus")) for i in shared)
    if base_wrong or cand_wrong:
        print(f"\n  캠퍼스 오답(치명): {base_wrong} → {cand_wrong}")

    for label, summary in (("baseline", base_summary), ("candidate", cand_summary)):
        metrics = summary.get("rag_metrics") or summary.get("metrics")
        if metrics:
            print(f"\n  {label} RAG 지표: "
                  + " · ".join(f"{k}={v:.3f}" for k, v in metrics.items() if isinstance(v, (int, float))))

    print()
    if total_lost == 0 and delta_all > 0:
        print("  판정: 악화 없이 개선 — 적용 가능.")
    elif delta_all > 0:
        print("  판정: 순개선이지만 악화 문항이 있다. 위 목록을 확인한 뒤 결정할 것.")
    elif delta_all == 0:
        print("  판정: 전체 통과 수 동일. 축 단위 뒤집힘을 보고 판단할 것.")
    else:
        print("  판정: 회귀. 적용하지 말 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
