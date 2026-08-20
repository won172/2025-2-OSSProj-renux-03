"""제목만 있는 공지를 근거 자리에서 뒤로 민다.

실제 답변에서 이런 근거 그룹이 나왔다.

    확인된 정보 2 — 2023년 통계데이터 활용대회
      공지 본문이 비어 있어 상세 내용은 공지 링크를 확인해야 함

3년 전 공지이고 DB의 `content` 길이가 0이며 첨부도 없다. 답을 뒷받침하지 못하면서
근거 그룹 하나를 통째로 차지했다. 공지 5,528건 중 1,445건(25.6%)이 본문 0자이고,
그중 914건은 첨부조차 없다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_RAG_SERVICE = (Path(__file__).resolve().parents[1] / "api" / "rag_service.py").read_text(
    encoding="utf-8"
)


def test_본문도_첨부도_없으면_근거로_쓸_수_없다고_표시한다():
    import pandas as pd

    from src.pipelines.ingest import build_notice_chunks

    frame = pd.DataFrame(
        [
            {
                "제목": "2023년 통계데이터 활용대회 안내",
                "본문": "",
                "게시일": "2023-05-15",
                "게시판": "학사공지",
                "상세URL": "https://example.test/a",
                "첨부파일": "[]",
                "clean_date": "2023-05-15",
                "db_id": 1,
            },
            {
                "제목": "2026학년도 2학기 학부 수강신청 안내",
                "본문": "수강신청 기간은 2026.08.03.부터입니다.",
                "게시일": "2026-07-22",
                "게시판": "학사공지",
                "상세URL": "https://example.test/b",
                "첨부파일": "[]",
                "clean_date": "2026-07-22",
                "db_id": 2,
            },
        ]
    )
    chunks = build_notice_chunks(frame)
    flags = dict(
        zip(
            chunks["title"].astype(str),
            chunks["has_substantive_body"].astype(str),
        )
    )
    빈문서 = next(v for k, v in flags.items() if "통계데이터" in k)
    본문있음 = next(v for k, v in flags.items() if "수강신청" in k)
    assert 빈문서 == "0"
    assert 본문있음 == "1"


def test_첨부가_있으면_근거로_남긴다():
    """본문이 비어도 첨부 링크로 안내할 수 있으면 값이 있다."""
    import pandas as pd

    from src.pipelines.ingest import build_notice_chunks

    frame = pd.DataFrame(
        [
            {
                "제목": "모집 요강",
                "본문": "",
                "게시일": "2026-07-01",
                "게시판": "학사공지",
                "상세URL": "https://example.test/c",
                "첨부파일": '[{"name": "요강.pdf", "url": "https://example.test/f.pdf"}]',
                "clean_date": "2026-07-01",
                "db_id": 3,
            }
        ]
    )
    chunks = build_notice_chunks(frame)
    assert chunks["has_substantive_body"].astype(str).tolist() == ["1"]


def test_정렬이_빈문서를_뒤로_민다():
    """관련도보다 뒤, 그러나 제외는 아니다 — 유일한 후보면 여전히 쓰인다."""
    sort_block = re.search(
        r"ranked\.sort_values\(\s*\[(.*?)\]", _RAG_SERVICE, re.S
    )
    assert sort_block is not None
    keys = sort_block.group(1)
    assert "_thin_document_rank" in keys
    # 관련도(_numeric_final)보다 앞서 비교돼야 근거 자리를 양보하게 된다.
    assert keys.index("_thin_document_rank") < keys.index("_numeric_final")


def test_강등이지_제외가_아니다():
    """`ranked.loc[~...]` 같은 제거가 아니라 정렬 키로만 다뤄야 한다."""
    assert "_thin_document_rank" in _RAG_SERVICE
    # is_latest는 의도적으로 제외(loc)를 쓰지만, 빈 문서는 제외하지 않는다.
    thin_lines = [
        line for line in _RAG_SERVICE.splitlines() if "_thin_document_rank" in line
    ]
    assert not any(".loc[~" in line for line in thin_lines)
