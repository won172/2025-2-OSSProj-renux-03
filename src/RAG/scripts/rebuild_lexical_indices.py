"""임베딩을 다시 계산하지 않고 희소 검색 인덱스만 재구축한다.

**왜 따로 필요한가.** `build_indices.py`는 SQLite 정본에서 청크를 다시 만들고
Chroma에 전량 재임베딩한다. 32,508청크를 KURE-v1로 다시 인코딩하는 일이라
수십 분이 걸린다. 그런데 토크나이저를 바꾸는 변경(Kiwi 도입 등)은 **밀집 벡터에
아무 영향이 없다** — 임베딩은 `retrieval_text`를 그대로 먹고, 토크나이저는
BM25/FTS5 색인에만 쓰인다.

그래서 이 스크립트는 parquet에 이미 있는 청크를 그대로 읽어 희소 인덱스만
다시 만든다. 청크 내용도 chunk_id도 바뀌지 않으므로 **qrels 라벨이 그대로 유효**하고,
`evaluate_retrieval.py --baseline` 비교가 토크나이저 효과만 분리해서 보여준다.

색인 대상 텍스트는 운영과 같은 `retrieval_text`(문맥 헤더 + 본문)다.
`ingest._persist_chunks`가 쓰는 것과 같은 값이어야 검색 결과가 일치한다.

사용:
    python scripts/rebuild_lexical_indices.py --backup-dir artifacts/rebuilds/lexical-before
    python scripts/rebuild_lexical_indices.py --datasets notices rules
    python scripts/rebuild_lexical_indices.py --restore artifacts/rebuilds/lexical-before
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import VECTORIZER_DIR  # noqa: E402
from src.pipelines.ingest import DATASET_ARTIFACTS, _train_lexical_indices  # noqa: E402
from src.search import fts_index as fts  # noqa: E402
from src.search.hybrid import _load_kiwi  # noqa: E402
from src.services.retrieval_context import enrich_retrieval_fields  # noqa: E402


def _artifact_paths() -> list[Path]:
    """백업·복원 대상. FTS5는 단일 파일, pkl은 데이터셋별 파일 + 매니페스트."""
    paths = [fts.fts_db_path()]
    paths.extend(sorted(VECTORIZER_DIR.glob("*_bm25.pkl")))
    manifest = VECTORIZER_DIR / "manifest.json"
    if manifest.exists():
        paths.append(manifest)
    return [path for path in paths if path.exists()]


def backup(destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in _artifact_paths():
        shutil.copy2(path, destination / path.name)
        copied += 1
    print(f"백업 {copied}개 → {destination}")
    return copied


def restore(source: Path) -> int:
    """되돌리기. 재색인 결과가 나쁘면 이 한 줄로 원상복구한다."""
    if not source.is_dir():
        print(f"백업 디렉터리가 없습니다: {source}")
        return 0
    restored = 0
    for path in sorted(source.iterdir()):
        if path.name == fts.fts_db_path().name:
            target = fts.fts_db_path()
        elif path.suffix in {".pkl", ".json"}:
            target = VECTORIZER_DIR / path.name
        else:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        restored += 1
    print(f"복원 {restored}개 ← {source}")
    return restored


def rebuild(datasets: list[str]) -> int:
    failures = 0
    for key in datasets:
        artifacts = DATASET_ARTIFACTS.get(key)
        if artifacts is None or not artifacts.chunk_path.exists():
            print(f"  ! {key}: 청크 파일이 없습니다 ({artifacts.chunk_path if artifacts else '?'})")
            failures += 1
            continue

        frame = enrich_retrieval_fields(pd.read_parquet(artifacts.chunk_path))
        if frame.empty:
            print(f"  ! {key}: 청크가 비어 있습니다")
            failures += 1
            continue

        texts = frame["retrieval_text"].fillna("").astype(str).tolist()
        chunk_ids = frame["chunk_id"].astype(str).tolist()
        started_at = time.perf_counter()
        try:
            _train_lexical_indices(key, texts, chunk_ids)
        except Exception as exc:  # noqa: BLE001 - 한 데이터셋 실패가 나머지를 막지 않는다
            print(f"  ✗ {key}: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        elapsed = time.perf_counter() - started_at
        print(f"  ✓ {key}: {len(texts):,}청크 · {elapsed:.1f}초")
    return failures


def show_state() -> None:
    """설정 이름이 아니라 인덱스에 실제로 무엇이 박혔는지 읽는다."""
    print(f"kiwipiepy: {'설치됨' if _load_kiwi() is not None else '없음(경량 폴백)'}")
    path = fts.fts_db_path()
    if not path.exists():
        print(f"FTS5 인덱스 없음: {path}")
        return
    import sqlite3

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT identifier, document_count, tokenizer, tokenizer_backend FROM lexical_meta"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"lexical_meta를 읽지 못했습니다: {exc}")
        return
    finally:
        connection.close()
    print(f"{'dataset':<10} {'docs':>8}  {'tokenizer':<10} backend")
    for identifier, count, tokenizer, backend in rows:
        print(f"{identifier:<10} {count:>8,}  {tokenizer:<10} {backend}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datasets", nargs="+", choices=list(DATASET_ARTIFACTS), metavar="DATASET")
    parser.add_argument("--backup-dir", type=Path, help="재색인 전에 현재 인덱스를 여기 복사")
    parser.add_argument("--restore", type=Path, help="백업에서 되돌리고 종료")
    parser.add_argument("--state", action="store_true", help="현재 인덱스 상태만 출력")
    args = parser.parse_args()

    if args.state:
        show_state()
        return 0
    if args.restore:
        restored = restore(args.restore)
        show_state()
        return 0 if restored else 1

    targets = args.datasets or list(DATASET_ARTIFACTS)
    if args.backup_dir:
        backup(args.backup_dir)

    print(f"\n희소 인덱스 재구축 ({', '.join(targets)})")
    print(f"토크나이저: {'kiwi' if _load_kiwi() is not None else 'light_korean'}")
    failures = rebuild(targets)

    print()
    show_state()
    if failures:
        print(f"\n{failures}개 데이터셋 실패")
        return 1
    print("\n다음: python scripts/evaluate_retrieval.py --baseline tests/baselines/retrieval_baseline.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
