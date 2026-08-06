"""SQLite FTS5 기반 희소(lexical) 검색 인덱스.

`artifacts/vectorizers/*_bm25.pkl` 12개(194MB)를 대체한다. pkl은 저장소에
`git add -f`로 추적되고 있어 재색인마다 바이너리 전체가 diff에 잡히며,
역직렬화 중 임의 코드 실행이 가능해 매니페스트·락·해시 검증이 따로 필요했다.
FTS5 인덱스는 SQLite 파일 하나이므로 그 계층이 통째로 필요 없다.

## 저장 위치

`rag_database.db`가 아니라 `artifacts/fts/lexical_fts.db`에 둔다.
운영 DB는 `journal_mode=delete`라 쓰기가 읽기를 막는데, 공지 재색인은 6시간마다
수만 행을 쓴다. 같은 파일에 넣으면 그동안 질의 로깅까지 멈춘다.

## 원자적 교체

임시 파일에 새 인덱스를 만든 뒤 `os.replace`로 바꾼다. 반쯤 지어진 인덱스가
검색에 노출되지 않도록 하는 것으로, pkl 경로가 이미 지키던 성질을 그대로 옮겼다.

## 점수 부호

SQLite `bm25()`는 **음수**이고 **작을수록 관련도가 높다.** 부호를 버리면
(`abs()`) 순위가 뒤집힌다. 여기서는 부호를 뒤집어(`-rank`) 양수 관련도로 만든
뒤 최댓값으로 나눈다. 기존 `BM25LexicalIndex.score`와 같은 0..1 계약이다.
`test_fts_index.py`가 이 방향을 고정한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import numpy as np

from src.config import ARTIFACT_DIR, TFIDF_TOKENIZER

logger = logging.getLogger(__name__)

FTS_DIR = ARTIFACT_DIR / "fts"
FTS_DB_NAME = "lexical_fts.db"

# 데이터셋 식별자는 테이블 이름으로 문자열 보간되므로 화이트리스트로 제한한다.
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def fts_db_path() -> Path:
    return FTS_DIR / FTS_DB_NAME


def _validate_identifier(identifier: str) -> str:
    if not _SAFE_IDENTIFIER.match(identifier or ""):
        raise ValueError(
            f"허용되지 않는 데이터셋 식별자입니다: {identifier!r} "
            "(소문자로 시작하는 영숫자·밑줄 32자 이내)"
        )
    return identifier


def _table_name(identifier: str) -> str:
    return f"lex_{_validate_identifier(identifier)}"


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def fts5_available(conn: Optional[sqlite3.Connection] = None) -> bool:
    """빌드된 SQLite가 FTS5를 지원하는지 확인한다."""
    owned = conn is None
    probe = conn or sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        probe.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        if owned:
            probe.close()


def build_match_expression(tokens: Sequence[str]) -> str:
    """토큰 목록을 FTS5 MATCH 식으로 만든다.

    각 토큰을 큰따옴표 문자열로 감싸 FTS5 연산자(`OR`, `NEAR`, `*`, `-` 등)로
    해석되는 것을 막는다. 문자열 안의 큰따옴표는 두 번 써서 이스케이프한다.
    """
    quoted = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens if t]
    return " OR ".join(quoted)


@dataclass
class Fts5LexicalIndex:
    """FTS5 인덱스 핸들. `BM25LexicalIndex`와 같은 `.score(query)` 계약을 만족한다."""

    identifier: str
    chunk_ids: List[str]
    tokenizer_name: str = TFIDF_TOKENIZER
    db_path: Path = field(default_factory=fts_db_path)

    @property
    def document_count(self) -> int:
        return len(self.chunk_ids)

    def score(self, query: str) -> np.ndarray:
        """질의에 대한 0..1 정규화 점수를 `chunk_ids` 순서에 맞춘 배열로 반환한다."""
        from src.search.hybrid import _kiwi_or_light_korean_tokenize, _light_korean_tokenize

        scores = np.zeros(self.document_count, dtype=np.float64)
        if self.document_count == 0:
            return scores

        tokens = (
            _kiwi_or_light_korean_tokenize(query)
            if self.tokenizer_name == "korean"
            else _light_korean_tokenize(query)
        )
        match_expr = build_match_expression(tokens)
        if not match_expr:
            return scores

        table = _table_name(self.identifier)
        try:
            conn = _connect(self.db_path, read_only=True)
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 인덱스를 열지 못했습니다(%s): %s", self.db_path, exc)
            return scores

        try:
            rows = conn.execute(
                f"SELECT rowid, bm25({table}) AS rank FROM {table} WHERE {table} MATCH ?",
                (match_expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # 구문 오류가 난 질의로 검색 전체를 죽이지 않는다. 희소 기여만 0이 된다.
            logger.warning("FTS5 질의 실패(%s, %r): %s", self.identifier, query, exc)
            return scores
        finally:
            conn.close()

        if not rows:
            return scores

        # bm25()는 음수이고 작을수록 관련도가 높다. 부호를 뒤집어 양수 관련도로 만든다.
        # abs()를 쓰면 순위가 뒤집힌다(v2 실험에서 실제로 발생한 결함).
        for row in rows:
            pos = int(row["rowid"])
            if 0 <= pos < self.document_count:
                scores[pos] = -float(row["rank"])

        positive_max = float(scores.max())
        if positive_max <= 0:
            return np.zeros_like(scores)
        return np.clip(scores / positive_max, 0.0, 1.0)


def build_fts_index(
    identifier: str,
    corpus: Iterable[str],
    chunk_ids: Iterable[str],
    *,
    db_path: Optional[Path] = None,
    tokenizer_name: Optional[str] = None,
) -> Fts5LexicalIndex:
    """한 데이터셋의 FTS5 인덱스를 (재)구축한다.

    같은 파일 안의 다른 데이터셋 테이블은 보존한다. 임시 사본에 쓴 뒤
    원자적으로 교체하므로, 실패해도 기존 인덱스가 남는다.
    """
    table = _table_name(identifier)
    texts = [str(t) for t in corpus]
    ids = [str(c) for c in chunk_ids]
    if len(ids) != len(texts):
        raise ValueError(
            f"chunk_ids 길이({len(ids)})가 corpus 길이({len(texts)})와 다릅니다."
        )
    if not texts:
        raise ValueError("코퍼스가 비어 있어 FTS5 인덱스를 만들 수 없습니다.")

    tokenizer = tokenizer_name or TFIDF_TOKENIZER
    target = db_path or fts_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    from src.search.hybrid import _kiwi_or_light_korean_tokenize, _light_korean_tokenize

    tokenize = (
        _kiwi_or_light_korean_tokenize if tokenizer == "korean" else _light_korean_tokenize
    )

    # 기존 파일을 임시 사본으로 복사해 다른 데이터셋 테이블을 보존한다.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        if target.exists():
            src = _connect(target, read_only=True)
            dst = _connect(tmp_path)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()

        conn = _connect(tmp_path)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS lexical_meta (
                       identifier TEXT PRIMARY KEY,
                       document_count INTEGER NOT NULL,
                       tokenizer TEXT NOT NULL,
                       chunk_ids TEXT NOT NULL
                   )"""
            )
            # content=''(contentless)로 토큰 원문을 저장하지 않는다. 필요한 것은
            # bm25() 점수와 행 위치뿐이고 본문은 parquet에 이미 있다. 저장하면
            # notices 기준 저장하면 34.7MB, 저장하지 않으면 9.3MB다.
            # 행 위치는 rowid로 직접 넘긴다(contentless에서도 rowid는 조회 가능).
            # contentless 테이블은 DELETE가 불가하므로 재구축 시 통째로 다시 만든다.
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute(
                f"""CREATE VIRTUAL TABLE {table} USING fts5(
                        tokens,
                        content = '',
                        tokenize = 'unicode61'
                    )"""
            )
            # rowid = chunk_ids 내 위치. 점수 배열을 그대로 이 순서에 맞춘다.
            conn.executemany(
                f"INSERT INTO {table} (rowid, tokens) VALUES (?, ?)",
                (
                    (pos, " ".join(tokenize(text)) or text)
                    for pos, text in enumerate(texts)
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO lexical_meta "
                "(identifier, document_count, tokenizer, chunk_ids) VALUES (?, ?, ?, ?)",
                (identifier, len(ids), tokenizer, json.dumps(ids, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info("FTS5 인덱스 구축 완료: %s (%d건) → %s", identifier, len(ids), target)
    return Fts5LexicalIndex(
        identifier=identifier,
        chunk_ids=ids,
        tokenizer_name=tokenizer,
        db_path=target,
    )


def load_fts_index(
    identifier: str, *, db_path: Optional[Path] = None
) -> Optional[Fts5LexicalIndex]:
    """저장된 FTS5 인덱스를 읽는다. 없으면 None을 반환한다(호출부가 폴백)."""
    _validate_identifier(identifier)
    target = db_path or fts_db_path()
    if not target.exists():
        return None
    try:
        conn = _connect(target, read_only=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT document_count, tokenizer, chunk_ids FROM lexical_meta WHERE identifier = ?",
            (identifier,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()

    if row is None:
        return None
    try:
        ids = [str(c) for c in json.loads(row["chunk_ids"])]
    except (json.JSONDecodeError, TypeError):
        logger.warning("FTS5 메타데이터의 chunk_ids를 읽지 못했습니다: %s", identifier)
        return None
    if len(ids) != int(row["document_count"]):
        logger.warning(
            "FTS5 메타데이터 불일치(%s): chunk_ids %d건 vs document_count %d",
            identifier, len(ids), row["document_count"],
        )
        return None

    return Fts5LexicalIndex(
        identifier=identifier,
        chunk_ids=ids,
        tokenizer_name=str(row["tokenizer"]),
        db_path=target,
    )
