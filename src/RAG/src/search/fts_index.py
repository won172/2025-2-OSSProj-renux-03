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
(`abs()`) 순위가 뒤집힌다. 여기서는 부호를 뒤집어(`-rank`) 양수 관련도로 만든다.
0..1 정규화는 `BM25LexicalIndex`와 마찬가지로 `hybrid.score_lexical_query`가
한 곳에서 하므로 여기서는 하지 않는다. `test_fts_index.py`가 이 방향을 고정한다.
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


def tokenizer_backend(tokenizer_name: str) -> str:
    """지금 실제로 쓰이는 토크나이저 구현 이름.

    `TFIDF_TOKENIZER="korean"`은 Kiwi가 설치돼 있으면 형태소 분석을, 없으면 경량
    폴백을 쓴다. 둘은 **어휘가 전혀 다르다** — Kiwi는 "교환학생"을 교환+학생으로
    쪼개고, 폴백은 복합어와 n-gram을 남긴다. 색인과 질의가 서로 다른 쪽을 쓰면
    히트가 조용히 사라진다. 그래서 이름("korean")이 아니라 실제 구현을 기록한다.
    """
    if tokenizer_name != "korean":
        return tokenizer_name
    from src.search.hybrid import _load_kiwi

    return "kiwi" if _load_kiwi() is not None else "light_korean"


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
        """질의에 대한 **원점수**를 `chunk_ids` 순서에 맞춘 배열로 반환한다.

        `BM25LexicalIndex.score`와 동일하게 정규화하지 않은 값을 돌려준다.
        0..1 정규화는 두 백엔드 공통으로 `hybrid.score_lexical_query`가 한 곳에서
        수행한다. 여기서 미리 정규화하면 백엔드를 바꿀 때 정규화 의미까지 함께
        바뀌어 두 백엔드를 비교할 수 없다.
        """
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
        return scores


def absent_terms(
    identifier: str, terms: Iterable[str], *, db_path: Optional[Path] = None
) -> List[str]:
    """주어진 토큰 중 이 데이터셋 코퍼스에 **한 번도 나오지 않는** 것을 돌려준다.

    "검색이 못 찾았다"와 "그런 데이터가 아예 없다"는 처방이 완전히 다르다.
    앞은 랭킹·필터 문제이고, 뒤는 사람이 자료를 채워야 하는 문제다. 그런데 폴백
    로그만 보면 둘이 똑같이 보인다. 골든 70건 실측에서 기대 키워드의 24.5%p가
    코퍼스 어디에도 없었다 — 검색으로는 절대 닿을 수 없는 몫이다.

    FTS5는 색인된 어휘 목록을 `fts5vocab`으로 그대로 노출하므로, 별도 자료구조
    없이 이 판정을 할 수 있다. pkl(rank_bm25) 백엔드로는 불가능했다.
    """
    table = _table_name(identifier)
    wanted = [t for t in {str(x).strip().lower() for x in terms} if t]
    if not wanted:
        return []

    target = db_path or fts_db_path()
    if not target.exists():
        return []
    try:
        conn = _connect(target, read_only=True)
    except sqlite3.OperationalError:
        return []

    try:
        # fts5vocab은 임시 DB에 만든다. 본 DB가 읽기 전용이어도 temp는 쓸 수 있다.
        conn.execute(f"CREATE VIRTUAL TABLE temp.vocab_probe USING fts5vocab(main, {table}, 'row')")
        placeholders = ",".join("?" for _ in wanted)
        present = {
            str(row[0])
            for row in conn.execute(
                f"SELECT term FROM temp.vocab_probe WHERE term IN ({placeholders})", wanted
            )
        }
    except sqlite3.OperationalError as exc:
        logger.warning("어휘 조회 실패(%s): %s", identifier, exc)
        return []
    finally:
        try:
            conn.execute("DROP TABLE IF EXISTS temp.vocab_probe")
        except sqlite3.OperationalError:
            pass
        conn.close()

    return [t for t in wanted if t not in present]


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
                       chunk_ids TEXT NOT NULL,
                       tokenizer_backend TEXT
                   )"""
            )
            # CREATE TABLE IF NOT EXISTS는 이미 있는 테이블에 컬럼을 더하지 않는다.
            # 기존 인덱스 파일을 복사해 이어 쓰므로 빠진 컬럼은 여기서 채운다.
            # (테스트는 매번 새 파일을 만들어 이 경로를 타지 않는다 — 실제 인덱스에서만 터졌다.)
            컬럼 = {r["name"] for r in conn.execute("PRAGMA table_info(lexical_meta)")}
            if "tokenizer_backend" not in 컬럼:
                conn.execute("ALTER TABLE lexical_meta ADD COLUMN tokenizer_backend TEXT")

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
                "(identifier, document_count, tokenizer, chunk_ids, tokenizer_backend) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, len(ids), tokenizer, json.dumps(ids, ensure_ascii=False),
                 tokenizer_backend(tokenizer)),
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
        try:
            row = conn.execute(
                "SELECT document_count, tokenizer, chunk_ids, tokenizer_backend "
                "FROM lexical_meta WHERE identifier = ?",
                (identifier,),
            ).fetchone()
        except sqlite3.OperationalError:
            # tokenizer_backend 이전에 만들어진 인덱스. 재색인하면 채워진다.
            row = conn.execute(
                "SELECT document_count, tokenizer, chunk_ids, NULL AS tokenizer_backend "
                "FROM lexical_meta WHERE identifier = ?",
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

    저장된_구현 = row["tokenizer_backend"]
    현재_구현 = tokenizer_backend(str(row["tokenizer"]))
    if 저장된_구현 and 저장된_구현 != 현재_구현:
        # Kiwi 설치·제거만으로 어휘가 통째로 바뀐다. 색인과 질의가 갈리면
        # 예외 없이 히트만 사라지므로, 조용히 나빠지지 않도록 크게 남긴다.
        logger.error(
            "'%s' FTS5 인덱스는 '%s' 토크나이저로 만들어졌는데 지금은 '%s'를 씁니다. "
            "어휘가 달라 희소 검색이 제대로 동작하지 않습니다 — 재색인이 필요합니다.",
            identifier, 저장된_구현, 현재_구현,
        )

    return Fts5LexicalIndex(
        identifier=identifier,
        chunk_ids=ids,
        tokenizer_name=str(row["tokenizer"]),
        db_path=target,
    )
