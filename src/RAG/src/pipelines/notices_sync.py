"""새로 수집한 공지를 DB에 저장하고 후속 산출물을 갱신하는 유틸리티입니다."""
from __future__ import annotations

from typing import Any
import json
import pandas as pd
from sqlalchemy import text

from src.database import engine, SessionLocal
from src.pipelines.ingest import build_notice_chunks, reindex_from_db # reindex_from_db 추가


def get_existing_notice_urls() -> set[str]:
    """데이터베이스에서 모든 공지의 상세 URL을 가져옵니다."""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT detail_url FROM notices"))
        return {row[0] for row in result}


def filter_new_rows(incoming: pd.DataFrame, existing_urls: set[str]) -> pd.DataFrame:
    """크롤링된 데이터(한글 컬럼)에서 신규 공지만 필터링하여 반환합니다."""
    if incoming.empty:
        return pd.DataFrame()
    new_mask = ~incoming["상세URL"].isin(existing_urls)
    return incoming[new_mask].copy()


from src.config import DATA_SOURCES

def save_new_notices_to_db(new_rows: pd.DataFrame) -> pd.DataFrame:
    """
    새로운 공지들을 데이터베이스의 notices 테이블에 저장하고,
    저장된 행(DB 컬럼명 기준)을 반환합니다.
    동시에 로컬 CSV 파일에도 데이터를 추가합니다.
    """
    if new_rows.empty:
        return pd.DataFrame()

    csv_path = DATA_SOURCES["notices"]
    
    # 1. 기존 CSV 파일 로드 (파일이 없으면 빈 DataFrame)
    if csv_path.exists():
        existing_df = pd.read_csv(csv_path)
    else:
        existing_df = pd.DataFrame(columns=new_rows.columns) # 새 파일 생성 시 컬럼명 일치
        
    # 2. 신규 데이터를 기존 데이터 위에 추가
    combined_df = pd.concat([new_rows, existing_df], ignore_index=True)
    combined_df.drop_duplicates(subset=['상세URL'], inplace=True) # 중복 제거
    
    # 3. CSV 파일 전체 덮어쓰기
    try:
        combined_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"CSV 파일 업데이트 완료. 총 {len(combined_df)}건.")
    except Exception as e:
        print(f"⚠️ CSV 파일 업데이트 실패: {e}")

    # 4. DB 스키마(영문 컬럼명)에 맞게 이름 변경
    notices_to_save = new_rows.rename(columns={
        "게시판": "board", "제목": "title", "카테고리": "category",
        "게시일": "published_date", "상단고정": "is_fixed", "상세URL": "detail_url",
        "본문": "content", "첨부파일": "attachments"
    })
    notices_to_save["published_date"] = pd.to_datetime(notices_to_save["published_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    notices_to_save["published_date"] = notices_to_save["published_date"].fillna("")

    # attachments 컬럼을 직렬화
    if "attachments" in notices_to_save.columns:
        notices_to_save["attachments"] = notices_to_save["attachments"].apply(_serialize_metadata)

    notices_to_save.to_sql("notices", con=engine, if_exists="append", index=False)
    return notices_to_save


def _serialize_metadata(value: Any) -> str:
    """메타데이터를 JSON 문자열로 직렬화합니다."""
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)
    if pd.isna(value):
        return ""
    return str(value)


def save_chunks_to_db(generated_chunks: pd.DataFrame, saved_notices: pd.DataFrame):
    """생성된 청크들을 상위 공지와 연결하여 데이터베이스의 chunks 테이블에 저장합니다."""
    if generated_chunks.empty or saved_notices.empty:
        return

    # DB에 저장된 공지의 detail_url과 id를 매핑
    url_to_id_map = saved_notices.set_index("detail_url")["id"].to_dict()

    # `build_notice_chunks`가 생성한 'url' 컬럼을 사용하여 notice_id 매핑
    generated_chunks["notice_id"] = generated_chunks["url"].map(url_to_id_map)

    chunks_to_save = generated_chunks[["chunk_id", "chunk_text", "notice_id"]].copy()
    chunks_to_save.dropna(subset=["notice_id"], inplace=True)
    chunks_to_save["notice_id"] = chunks_to_save["notice_id"].astype(int)

    # --- 중복 방지 로직 추가 ---
    # 1. 현재 DB에 저장된 chunk_id 목록을 가져옵니다.
    existing_chunk_ids = set()
    if not chunks_to_save.empty:
        chunk_ids_to_check = chunks_to_save["chunk_id"].tolist()
        # chunk_ids_to_check가 너무 많으면 IN 절이 실패할 수 있으므로 나눠서 조회하거나 전체를 조회해야 함.
        # 여기서는 간단하게 전체 청크 ID를 가져오지 않고, 저장하려는 ID 중에서 이미 있는 것만 확인.
        
        # 청크 ID 리스트를 문자열로 변환하여 쿼리에 사용
        id_list_str = ",".join([f"'{cid}'" for cid in chunk_ids_to_check])
        
        # 데이터가 많을 경우를 대비해 chunk_id만 조회
        # (SQLite에서는 IN 절 제한이 있을 수 있으나 수천 개 수준은 괜찮음)
        with engine.connect() as conn:
             # 간단하게, 저장하려는 ID들 중 이미 존재하는 ID만 조회
             # 너무 길어질 수 있으니, 그냥 chunks 테이블의 모든 chunk_id를 가져오는 것은 비효율적일 수 있지만,
             # 현재 구조상 update_notices.py는 '신규' 공지만 처리하므로 여기서 중복이 발생하는 건
             # 이전 실행 실패 등으로 찌꺼기가 남았을 때임.
             # 안전하게 처리하기 위해, 반복문으로 처리하거나 temp 테이블을 쓸 수 있으나,
             # 여기서는 pandas의 read_sql로 현재 저장하려는 ID들이 있는지 확인함.
             
             # 청크가 너무 많으면(예: 1000개 이상) 나눠서 처리해야 함.
             existing_df = pd.read_sql(f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({id_list_str})", conn)
             existing_chunk_ids = set(existing_df["chunk_id"].tolist())

    # 2. 이미 존재하는 ID는 제외
    if existing_chunk_ids:
        chunks_to_save = chunks_to_save[~chunks_to_save["chunk_id"].isin(existing_chunk_ids)]

    if not chunks_to_save.empty:
        chunks_to_save.to_sql("chunks", con=engine, if_exists="append", index=False)


def sync_notices(incoming_df: pd.DataFrame) -> int:
    """
    새 공지를 DB에 저장하고, 청크 생성 및 DB 저장,
    Chroma/TF-IDF 갱신 후 신규 공지 수를 반환합니다.
    """
    # 1. DB에서 기존 URL 목록을 가져와 신규 공지 필터링
    existing_urls = get_existing_notice_urls()
    new_notices_korean_cols = filter_new_rows(incoming_df, existing_urls)

    if new_notices_korean_cols.empty:
        print("신규 공지가 없습니다.")
        return 0

    # 2. 신규 공지를 DB에 저장하고, 저장된 데이터(영문 컬럼)를 반환받음
    #    이때, ID를 얻기 위해 DB에서 다시 읽어옴
    save_new_notices_to_db(new_notices_korean_cols)
    
    saved_notice_urls = [f"'{url}'" for url in new_notices_korean_cols['상세URL'].unique()]
    saved_notices_df = pd.read_sql(
        f"SELECT id, detail_url FROM notices WHERE detail_url IN ({', '.join(saved_notice_urls)})",
        engine
    )

    print(f"{len(saved_notices_df)}건의 신규 공지를 데이터베이스에 저장했습니다.")

    # 3. 신규 공지(한글 컬럼)로 청크 생성
    generated_chunks = build_notice_chunks(new_notices_korean_cols)

    # 4. 생성된 청크를 DB에 저장 (이때 부모 공지 ID를 연결)
    save_chunks_to_db(generated_chunks, saved_notices_df)
    print(f"{len(generated_chunks)}개의 신규 청크를 데이터베이스에 저장했습니다.")

    # 5. ChromaDB 업데이트 및 TF-IDF 재학습 (DB 기반 재색인)
    # 이제 CSV가 아닌 DB에 저장된 청크 데이터를 기반으로 인덱스를 업데이트합니다.
    # 증분 색인(upsert/delete) 로직이 reindex_from_db 내부에 구현되어 있습니다.
    reindex_from_db("notices")
    print("ChromaDB 인덱스와 TF-IDF 모델을 업데이트했습니다.")

    return len(saved_notices_df)

__all__ = ["sync_notices"]
