"""테스트가 운영 아티팩트를 건드리지 못하게 막는다.

실제로 일어난 사고에서 나왔다. `persist_dataset_artifacts_only`에 FTS5 색인
구축을 붙였더니(S1.3), 그 함수를 부르던 기존 테스트가 **운영 인덱스를 덮어썼다.**
그 테스트는 잘못이 없었다 — `train_bm25`를 가짜로 바꾸고 청크 경로도 tmp로
돌려 스스로를 제대로 격리하고 있었다. 나중에 추가된 부작용을 알 수 없었을 뿐이다.

결과는 조용했다. notices 색인이 11,279건에서 1건이 되었지만, 행 수가 메타와
일치해(1 == 1) 검색은 예외 없이 계속 돌았고 희소 검색 기여만 사라졌다.
테스트는 전부 통과했다.

그래서 개별 테스트를 고치는 대신 경로 자체를 막는다. 앞으로 어떤 코드에
색인 부작용이 붙어도 테스트가 운영 파일에 닿지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _운영_FTS_인덱스_보호(tmp_path_factory, monkeypatch):
    """FTS5 색인 경로를 테스트마다 임시 파일로 돌린다.

    실제 인덱스를 읽어야 하는 테스트는 `db_path=`를 명시하면 된다.
    개발자 머신의 아티팩트에 기대는 테스트는 애초에 CI에서 재현되지 않는다.
    """
    import src.search.fts_index as fts_index

    임시 = tmp_path_factory.mktemp("fts") / "lexical_fts.db"
    monkeypatch.setattr(fts_index, "fts_db_path", lambda: 임시)
    yield
