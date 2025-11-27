"""새로 수집한 공지를 DB에 저장하고 후속 산출물을 갱신하는 유틸리티입니다."""
from __future__ import annotations

from typing import Any
import json
import pandas as pd
from sqlalchemy import text

from src.database import engine
from src.models.embedding import encode_texts
from src.pipelines.ingest import build_notice_chunks, DATASET_ARTIFACTS
from src.search.hybrid import train_tfidf
from src.vectorstore.chroma_client import add_items


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


def save_new_notices_to_db(new_rows: pd.DataFrame) -> pd.DataFrame:
    """
    새로운 공지들을 데이터베이스의 notices 테이블에 저장하고,
    저장된 행(DB 컬럼명 기준)을 반환합니다.
    """
    if new_rows.empty:
        return pd.DataFrame()

    # DB 스키마(영문 컬럼명)에 맞게 이름 변경
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

    chunks_to_save.to_sql("chunks", con=engine, if_exists="append", index=False)


def update_chroma_and_retrain_tfidf(new_chunks: pd.DataFrame):
    """새로운 청크로 ChromaDB를 업데이트하고, 전체 청크로 TF-IDF를 재학습합니다."""
    # ChromaDB 업데이트
    if not new_chunks.empty:
        embeddings = encode_texts(new_chunks["chunk_text"].tolist())
        add_items(
            DATASET_ARTIFACTS["notices"].collection,
            ids=new_chunks["chunk_id"],
            documents=new_chunks["chunk_text"],
            metadatas=new_chunks.drop(columns=["chunk_text"]).to_dict(orient="records"),
            embeddings=embeddings,
        )

    # TF-IDF 재학습 (DB의 모든 청크 사용)
    all_chunks_df = pd.read_sql("SELECT chunk_text FROM chunks", engine)
    if not all_chunks_df.empty:
        train_tfidf("notices", all_chunks_df["chunk_text"].tolist())


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

    # 5. ChromaDB 업데이트 및 TF-IDF 재학습
    update_chroma_and_retrain_tfidf(generated_chunks)
    print("ChromaDB 인덱스와 TF-IDF 모델을 업데이트했습니다.")

    return len(saved_notices_df)

__all__ = ["sync_notices"]
