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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_chroma: 로컬 아티팩트 통합 검사처럼 운영 Chroma 경로를 명시적으로 읽어야 하는 테스트",
    )


@pytest.fixture(autouse=True)
def _운영_Chroma_보호(request, tmp_path_factory, monkeypatch):
    """Chroma 클라이언트를 테스트마다 임시 디렉터리로 돌리고 싱글턴을 비운다.

    `chroma_client.get_client()`는 프로세스 전역 싱글턴이고 운영
    `artifacts/db_chroma`를 연다. `count_items`를 가짜로 바꾸지 않은 테스트
    (readiness의 dense count probe)가 운영 Chroma를 열어, 빈 checkout에서는
    `chroma.sqlite3`를 새로 만들고 데이터가 있는 checkout에서는 운영 파일을 갱신했다.
    싱글턴과 컬렉션 LRU 캐시를 테스트마다 비워 이전 테스트의 클라이언트가 남지 않게 한다.

    `src.config.CHROMA_DIR`는 바꾸지 않는다. staged rebuild 안전 검사는 그 값을
    운영 경로로 보고 거부해야 하므로 그대로 두는 편이 더 엄격하다.

    실제 로컬 인덱스를 읽는 통합 smoke 검사는 `@pytest.mark.real_chroma`로 명시한다.
    그때도 싱글턴·캐시는 비워 다른 테스트와 클라이언트를 공유하지 않는다.
    """
    from src.vectorstore import chroma_client

    # 테스트가 `_get_physical_collection`을 가짜 함수로 바꿀 수 있으므로(그 undo는
    # 이 fixture 정리 뒤에 일어난다) 실제 LRU 함수를 먼저 잡아 두고 그것을 비운다.
    실제_컬렉션_캐시 = chroma_client._get_physical_collection
    if request.node.get_closest_marker("real_chroma") is None:
        임시 = tmp_path_factory.mktemp("chroma") / "db_chroma"
        monkeypatch.setattr(chroma_client, "CHROMA_DIR", 임시)
    monkeypatch.setattr(chroma_client, "_client_instance", None)
    실제_컬렉션_캐시.cache_clear()
    yield
    실제_컬렉션_캐시.cache_clear()


@pytest.fixture(autouse=True)
def _운영_유지보수_잠금_보호(tmp_path_factory, monkeypatch):
    """공용 유지보수 잠금 파일을 테스트마다 임시 경로로 돌린다.

    기본 경로는 운영 `artifacts/.rag-maintenance.lock`이다. 테스트가 이 잠금을 잡는
    동안 같은 checkout의 스케줄러·관리 작업은 `MaintenanceLockBusy`로 거부된다.
    """
    from src.services.maintenance_lock import MAINTENANCE_LOCK_ENV

    임시 = tmp_path_factory.mktemp("maintenance") / ".rag-maintenance.lock"
    monkeypatch.setenv(MAINTENANCE_LOCK_ENV, str(임시))
    yield
