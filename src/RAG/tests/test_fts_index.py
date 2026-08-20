"""FTS5 희소 검색 인덱스 계약 테스트.

가장 중요한 것은 **점수 부호**다. SQLite `bm25()`는 음수이고 작을수록 관련도가
높은데, v2 실험에서 `1/(1+abs(rank))`로 정규화하는 바람에 관련도가 높은 문서일수록
점수가 낮아졌다. `ORDER BY rank ASC`로 뽑은 순서만 쓰면 드러나지 않다가,
점수로 정렬하거나 임계값을 거는 순간 최악을 고르는 형태였다.
아래 `test_관련도가_높은_문서가_높은_점수를_받는다`가 그 방향을 고정한다.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.search.fts_index import (  # noqa: E402
    Fts5LexicalIndex,
    build_fts_index,
    build_match_expression,
    fts5_available,
    load_fts_index,
)

pytestmark = pytest.mark.skipif(
    not fts5_available(), reason="이 SQLite 빌드는 FTS5를 지원하지 않습니다"
)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "lexical_fts.db"


def _build(db: Path, identifier: str, docs: dict[str, str]) -> Fts5LexicalIndex:
    return build_fts_index(
        identifier, list(docs.values()), list(docs.keys()), db_path=db
    )


# --- 점수 부호 (v2 회귀 방지) -------------------------------------------------


def test_관련도가_높은_문서가_높은_점수를_받는다(db):
    """짧고 질의어가 밀집한 문서가 길게 희석된 문서보다 높아야 한다.

    질의어를 갖지 않는 잡음 문서를 함께 넣어 IDF가 실제로 작동하게 한다.
    문서가 둘뿐이면 IDF가 0에 수렴해 bm25 값이 모두 -0.0000이 되고,
    잘못된 정규화를 써도 테스트가 통과해 버린다.

    실측(잡음 50건): raw bm25 밀집 -20.47 / 희석 -14.38
      - `-rank`         → 밀집 20.47 > 희석 14.38  (맞음)
      - `1/(1+abs())`   → 밀집 0.047 < 희석 0.065  (뒤집힘, v2가 이 형태였다)
    """
    문서 = {
        "밀집": "수강신청 수강신청 안내",
        "희석": "수강신청 " + "다른 내용이 계속 이어지는 문서입니다 " * 40,
    }
    문서.update({f"잡음{i}": f"학식 메뉴 장학금 도서관 공지 {i}" for i in range(50)})
    index = _build(db, "notices", 문서)

    점수 = index.score("수강신청")
    밀집 = 점수[index.chunk_ids.index("밀집")]
    희석 = 점수[index.chunk_ids.index("희석")]

    assert 밀집 > 희석, f"부호가 뒤집혔습니다: 밀집={밀집}, 희석={희석}"
    assert 밀집 > 0, "관련 문서의 원점수는 양수여야 한다"
    assert 점수[index.chunk_ids.index("잡음0")] == 0.0


def test_질의어가_없는_문서는_0점이다(db):
    index = _build(db, "notices", {
        "맞음": "수강신청 기간 안내",
        "무관": "오늘의 학식 메뉴는 제육볶음입니다",
    })
    점수 = index.score("수강신청")
    assert 점수[0] > 0
    assert 점수[1] == 0.0


def test_원점수를_반환하고_정규화하지_않는다(db):
    """정규화는 두 백엔드 공통으로 hybrid.score_lexical_query가 한 곳에서 한다.

    여기서 미리 0..1로 만들면 백엔드 교체가 정규화 의미까지 바꿔
    pkl과 FTS5를 비교할 수 없게 된다.
    """
    문서 = {f"장학{i}": f"장학금 신청 안내 {i}" for i in range(5)}
    문서.update({f"잡음{i}": f"학식 메뉴 도서관 공지 {i}" for i in range(50)})
    index = _build(db, "notices", 문서)

    점수 = index.score("장학금 신청")
    assert 점수.min() >= 0.0
    assert 점수.max() > 1.0, "원점수는 1을 넘을 수 있어야 한다(정규화되지 않음)"


# --- 행 정렬 계약 -------------------------------------------------------------


def test_점수_배열이_chunk_ids_순서와_맞는다(db):
    """호출부는 argsort 결과를 chunk_ids 인덱스로 되돌려 쓴다.

    (`hybrid.py`: `sparse_scores[row_ids[idx]] = sparse_sims[idx]`)
    따라서 배열 i번째는 반드시 chunk_ids[i]의 점수여야 한다.
    """
    index = _build(db, "notices", {
        "가": "휴학 신청 방법",
        "나": "복학 신청 방법",
        "다": "휴학 휴학 휴학",
    })
    점수 = index.score("휴학")
    최고 = index.chunk_ids[int(np.argmax(점수))]
    assert 최고 == "다"
    assert 점수[index.chunk_ids.index("나")] == 0.0


def test_점수_배열_길이가_문서_수와_같다(db):
    index = _build(db, "notices", {f"c{i}": f"공지 {i}" for i in range(7)})
    assert index.document_count == 7
    assert index.score("공지").shape == (7,)
    assert index.score("전혀없는단어").shape == (7,)


# --- 질의 이스케이프 ----------------------------------------------------------


def test_FTS5_연산자가_섞인_질의도_죽지_않는다(db):
    """토큰이 FTS5 구문으로 해석되면 OperationalError로 검색 전체가 죽는다."""
    index = _build(db, "notices", {"가": "장학금 안내"})
    for 질의 in ['장학금 OR NEAR', '장학금*', '-장학금', '장학금 AND', '"따옴표"']:
        점수 = index.score(질의)
        assert 점수.shape == (1,), f"{질의!r}에서 실패"


def test_큰따옴표가_이스케이프된다():
    assert build_match_expression(['가"나']) == '"가""나"'
    assert build_match_expression(["가", "나"]) == '"가" OR "나"'
    assert build_match_expression([]) == ""


def test_토큰이_없으면_0_배열이다(db):
    index = _build(db, "notices", {"가": "장학금 안내"})
    assert index.score("!!!").tolist() == [0.0]
    assert index.score("").tolist() == [0.0]


# --- 식별자 검증 --------------------------------------------------------------


@pytest.mark.parametrize("나쁜값", [
    "notices; DROP TABLE lexical_meta",
    "lex-notices",
    "Notices",
    "1notices",
    "",
    "n" * 40,
])
def test_위험한_식별자는_거부된다(db, 나쁜값):
    """식별자는 테이블 이름으로 보간되므로 화이트리스트로 막는다."""
    with pytest.raises(ValueError):
        build_fts_index(나쁜값, ["본문"], ["c1"], db_path=db)


# --- 재구축과 원자성 ----------------------------------------------------------


def test_한_데이터셋_재구축이_다른_데이터셋을_보존한다(db):
    _build(db, "notices", {"n1": "공지 본문"})
    _build(db, "courses", {"c1": "교과목 본문"})
    _build(db, "notices", {"n2": "새 공지 본문"})

    공지 = load_fts_index("notices", db_path=db)
    교과목 = load_fts_index("courses", db_path=db)
    assert 공지 is not None and 공지.chunk_ids == ["n2"]
    assert 교과목 is not None and 교과목.chunk_ids == ["c1"]


def test_구축_실패시_기존_인덱스가_남는다(db, monkeypatch):
    _build(db, "notices", {"n1": "공지 본문"})

    import src.search.fts_index as fts

    def 폭발(*a, **k):
        raise RuntimeError("토큰화 실패")

    monkeypatch.setattr(fts, "_connect", 폭발)
    with pytest.raises(RuntimeError):
        build_fts_index("notices", ["새 본문"], ["n2"], db_path=db)

    monkeypatch.undo()
    남은 = load_fts_index("notices", db_path=db)
    assert 남은 is not None and 남은.chunk_ids == ["n1"]


def test_임시파일이_남지_않는다(db):
    _build(db, "notices", {"n1": "공지 본문"})
    _build(db, "notices", {"n2": "다시 공지"})
    남은_임시 = list(db.parent.glob(f".{db.name}.*"))
    assert 남은_임시 == []


# --- 입력 검증 ----------------------------------------------------------------


def test_길이가_다르면_거부한다(db):
    with pytest.raises(ValueError, match="길이"):
        build_fts_index("notices", ["가", "나"], ["c1"], db_path=db)


def test_빈_코퍼스는_거부한다(db):
    with pytest.raises(ValueError):
        build_fts_index("notices", [], [], db_path=db)


# --- 로드 폴백 ----------------------------------------------------------------


def test_인덱스가_없으면_None을_반환한다(db):
    assert load_fts_index("notices", db_path=db) is None
    _build(db, "courses", {"c1": "교과목"})
    assert load_fts_index("notices", db_path=db) is None


def test_저장했다_읽으면_같은_점수가_나온다(db):
    원본 = _build(db, "notices", {
        "가": "수강신청 정정 기간",
        "나": "학식 메뉴",
    })
    읽음 = load_fts_index("notices", db_path=db)
    assert 읽음 is not None
    np.testing.assert_allclose(원본.score("수강신청"), 읽음.score("수강신청"))


def test_메타데이터가_깨지면_None을_반환한다(db):
    _build(db, "notices", {"가": "본문"})
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE lexical_meta SET chunk_ids = ? WHERE identifier = ?", ("{깨짐", "notices"))
    conn.commit()
    conn.close()
    assert load_fts_index("notices", db_path=db) is None


# --- 토크나이저 불일치 감지 ----------------------------------------------------


def test_토크나이저_구현이_바뀌면_크게_남긴다(db, caplog, monkeypatch):
    """Kiwi 설치·제거만으로 어휘가 통째로 바뀐다.

    TFIDF_TOKENIZER는 둘 다 "korean"이라 이름으로는 구분되지 않는다.
    실측: 같은 질의에 Kiwi는 ['교환','학생'], 폴백은 ['교환학생','교환','환학',…]를
    낸다. 색인과 질의가 갈리면 예외 없이 히트만 사라진다.
    """
    import logging

    import src.search.fts_index as fts

    monkeypatch.setattr(fts, "tokenizer_backend", lambda _n: "light_korean")
    _build(db, "notices", {"n1": "교환학생 지원 안내"})

    monkeypatch.setattr(fts, "tokenizer_backend", lambda _n: "kiwi")
    with caplog.at_level(logging.ERROR):
        idx = load_fts_index("notices", db_path=db)

    assert idx is not None, "경고만 하고 인덱스는 계속 쓸 수 있어야 한다"
    assert "재색인이 필요합니다" in caplog.text
    assert "light_korean" in caplog.text and "kiwi" in caplog.text


def test_같은_토크나이저면_조용하다(db, caplog):
    import logging

    _build(db, "notices", {"n1": "교환학생 지원 안내"})
    with caplog.at_level(logging.ERROR):
        assert load_fts_index("notices", db_path=db) is not None
    assert caplog.text == ""


def test_구버전_인덱스는_경고_없이_읽힌다(db, caplog):
    """tokenizer_backend 컬럼이 없던 인덱스도 계속 읽혀야 한다."""
    import logging
    import sqlite3

    _build(db, "notices", {"n1": "본문"})
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE lexical_meta SET tokenizer_backend = NULL")
    conn.commit()
    conn.close()

    with caplog.at_level(logging.ERROR):
        assert load_fts_index("notices", db_path=db) is not None
    assert caplog.text == ""


def test_구버전_스키마_파일에도_재색인이_된다(db):
    """CREATE TABLE IF NOT EXISTS는 기존 테이블에 컬럼을 더하지 않는다.

    새 파일만 쓰는 테스트는 이 경로를 타지 않아, 실제 인덱스에서만
    'no column named tokenizer_backend'로 터졌다.
    """
    import sqlite3

    _build(db, "notices", {"n1": "본문"})
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE lexical_meta DROP COLUMN tokenizer_backend")
    conn.commit()
    conn.close()

    _build(db, "notices", {"n2": "새 본문"})  # 여기서 터지면 안 된다
    idx = load_fts_index("notices", db_path=db)
    assert idx is not None and idx.chunk_ids == ["n2"]


# --- 어휘 커버리지 진단 --------------------------------------------------------


def test_코퍼스에_없는_낱말을_가려낸다(db):
    """"검색이 못 찾았다"와 "그런 자료가 아예 없다"는 처방이 다르다.

    폴백 로그만으로는 둘이 똑같이 보인다. 골든 70건에서 기대 키워드의 24.5%p가
    코퍼스에 아예 없었고, 그건 검색으로 절대 닿을 수 없는 몫이다.
    """
    from src.search.fts_index import absent_terms

    _build(db, "notices", {"n1": "장학금 신청 안내", "n2": "휴학 절차"})
    assert absent_terms("notices", ["장학금", "휴학"], db_path=db) == []
    assert absent_terms("notices", ["총학생회비"], db_path=db) == ["총학생회비"]

    섞임 = absent_terms("notices", ["장학금", "총학생회비", "보강"], db_path=db)
    assert set(섞임) == {"총학생회비", "보강"}


def test_어휘_조회가_인덱스를_바꾸지_않는다(db):
    """진단용 임시 테이블이 인덱스에 남으면 다음 재구축이 깨진다."""
    import sqlite3

    from src.search.fts_index import absent_terms

    _build(db, "notices", {"n1": "장학금 안내"})
    absent_terms("notices", ["총학생회비"], db_path=db)

    conn = sqlite3.connect(str(db))
    남은 = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%vocab%'")]
    conn.close()
    assert 남은 == []
    _build(db, "notices", {"n2": "재구축"})  # 여기서 터지면 안 된다


def test_인덱스가_없으면_빈_목록이다(db):
    from src.search.fts_index import absent_terms

    assert absent_terms("notices", ["아무말"], db_path=db) == []
