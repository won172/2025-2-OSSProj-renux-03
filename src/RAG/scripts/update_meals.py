"""학식(학생식당 식단) 데이터를 크롤링해 CSV와 Chroma/TF-IDF 인덱스를 갱신합니다.

학식은 매일 바뀌므로 정기 실행이 필요하다. 단발 실행 또는 `--interval` 반복 실행을 지원한다.

사용 예:
  python scripts/update_meals.py                 # 1회 갱신
  python scripts/update_meals.py --interval 1440 # 하루(1440분)마다 반복 (docker worker용)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.crawlers.dongguk_meals import crawl_meals
from src.config import DATA_SOURCES
from src.pipelines.ingest import ingest_meals
from src.database import init_db


def _run_once(days_ahead: int, delay: float, include_dflex: bool) -> bool:
    """학식을 크롤링해 CSV 저장 후 meals 인덱스를 재구축한다. 성공 시 True."""
    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{start_ts}] 🍚 학식(meals) 갱신 시작")

    try:
        df = crawl_meals(days_ahead=days_ahead, delay=delay, include_dflex=include_dflex)
    except Exception as exc:  # noqa: BLE001 — 크롤 실패가 워커 전체를 죽이지 않도록
        print(f"⚠️ 학식 크롤링 실패: {exc}")
        return False

    if df.empty:
        # 전 식당이 비었다면 사이트 구조 변경/차단일 수 있으므로 기존 인덱스를 보존(덮어쓰지 않음).
        print("⚠️ 수집된 학식 레코드가 0건 — 인덱스를 갱신하지 않고 기존 데이터를 보존합니다.")
        return False

    out_path = DATA_SOURCES["meals"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    n_dates = df["date"].nunique()
    n_rest = df["restaurant"].nunique()
    print(f"✅ {len(df)}행 저장 ({n_dates}일 × 식당 {n_rest}곳) → {out_path}")

    try:
        chunks_df, _, _ = ingest_meals()
        print(f"✅ meals 인덱스 재구축 완료: {len(chunks_df)} chunks")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ meals 인덱싱 실패: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="학식(meals) 데이터 갱신")
    parser.add_argument("--days-ahead", type=int, default=13, help="오늘 이후 며칠까지 수집할지 (기본 13)")
    parser.add_argument("--delay", type=float, default=0.4, help="요청 간 지연 초 (기본 0.4)")
    parser.add_argument("--no-dflex", action="store_true", help="경영관 D-Flex PDF 수집 제외")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="N분마다 반복 실행 (0 = 1회만). 일일 갱신은 1440 권장.",
    )
    args = parser.parse_args()

    init_db()
    include_dflex = not args.no_dflex

    if args.interval <= 0:
        _run_once(args.days_ahead, args.delay, include_dflex)
        return

    interval_seconds = args.interval * 60
    try:
        while True:
            _run_once(args.days_ahead, args.delay, include_dflex)
            print(f"⏳ {args.interval}분 후 다음 학식 갱신을 실행합니다. (종료: Ctrl+C)")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("🛑 학식 갱신 반복을 종료합니다.")


if __name__ == "__main__":
    main()
