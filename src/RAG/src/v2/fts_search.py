"""SQLite FTS5 기반 고성능 Sparse 검색 엔진 (Pickle 무의존성)

기존 TF-IDF joblib.pkl 파이프라인 대신, SQLite에 내장된 FTS5(Full-Text Search)와
Kiwi/경량 형태소 분석 토크나이저를 조합하여 < 5ms 이내의 극초고속 Sparse 검색을 제공합니다.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.database import DATABASE_FILE
from src.search.hybrid import _kiwi_or_light_korean_tokenize

logger = logging.getLogger(__name__)

FTS_TABLE_NAME = "chunks_fts"


def get_fts_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DATABASE_FILE
    conn = sqlite3.connect(str(target_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_fts_table(conn: sqlite3.Connection) -> None:
    """FTS5 가상 테이블 및 인덱스 구조 생성"""
    cursor = conn.cursor()
    
    # FTS5 테이블 존재 여부 확인 후 생성
    cursor.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE_NAME} USING fts5(
            chunk_id UNINDEXED,
            dataset_type UNINDEXED,
            source_id UNINDEXED,
            tokens,
            content UNINDEXED,
            tokenize = 'unicode61'
        );
    """)
    conn.commit()


def populate_fts_index(conn: sqlite3.Connection, batch_size: int = 500) -> int:
    """SQLite 'chunks' 테이블의 원문 데이터를 토큰화하여 FTS5 테이블로 색인 구축"""
    cursor = conn.cursor()
    
    # 먼저 FTS 테이블 비우기 (재현성 확보)
    cursor.execute(f"DELETE FROM {FTS_TABLE_NAME}")
    conn.commit()
    
    # SQLite chunks 조인 및 읽기
    cursor.execute("""
        SELECT id, chunk_id, chunk_text, doc_id, notice_id, rule_id, schedule_id, course_id, staff_id, custom_knowledge_id
        FROM chunks
    """)
    
    rows = cursor.fetchall()
    if not rows:
        logger.warning("FTS 색인을 구축할 chunks 데이터가 없습니다.")
        return 0
    
    total_count = len(rows)
    logger.info(f"FTS5 색인 구축 시작: 총 {total_count}개 청크 처리 중...")
    
    insert_records = []
    start_time = time.time()
    
    for row in rows:
        chunk_pk = str(row["id"])
        content = row["chunk_text"] or ""
        
        if row["notice_id"]:
            dataset_type = "notices"
            source_id = str(row["notice_id"])
        elif row["rule_id"]:
            dataset_type = "rules"
            source_id = str(row["rule_id"])
        elif row["schedule_id"]:
            dataset_type = "schedule"
            source_id = str(row["schedule_id"])
        elif row["course_id"]:
            dataset_type = "courses"
            source_id = str(row["course_id"])
        elif row["staff_id"]:
            dataset_type = "staff"
            source_id = str(row["staff_id"])
        elif row["custom_knowledge_id"]:
            dataset_type = "custom_knowledge"
            source_id = str(row["custom_knowledge_id"])
        else:
            dataset_type = "general"
            source_id = str(row["doc_id"] or row["id"])
        
        # 형태소/경량 토큰 추출 후 공백으로 연결
        tokens_list = _kiwi_or_light_korean_tokenize(content)
        tokens_str = " ".join(tokens_list) if tokens_list else content
        
        insert_records.append((chunk_pk, dataset_type, source_id, tokens_str, content))
        
        if len(insert_records) >= batch_size:
            cursor.executemany(
                f"INSERT INTO {FTS_TABLE_NAME} (chunk_id, dataset_type, source_id, tokens, content) VALUES (?, ?, ?, ?, ?)",
                insert_records
            )
            conn.commit()
            insert_records.clear()
            
    if insert_records:
        cursor.executemany(
            f"INSERT INTO {FTS_TABLE_NAME} (chunk_id, dataset_type, source_id, tokens, content) VALUES (?, ?, ?, ?, ?)",
            insert_records
        )
        conn.commit()
        
    elapsed = time.time() - start_time
    logger.info(f"FTS5 색인 구축 완료! ({total_count}건, {elapsed:.2f}초 소요)")
    return total_count


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    dataset_type: Optional[str] = None,
    top_k: int = 10
) -> List[Dict[str, Any]]:
    """SQLite FTS5 BM25 알고리즘 기반 초고속 검색

    Args:
        conn: sqlite3 커넥션
        query: 검색 질의
        dataset_type: notices, rules, schedule 등 필터링 (선택)
        top_k: 반환할 상위 항목 수

    Returns:
        검색 결과 리스트 [{'chunk_id', 'dataset_type', 'source_id', 'content', 'score'}]
    """
    cursor = conn.cursor()
    
    # 쿼리 토큰화
    query_tokens = _kiwi_or_light_korean_tokenize(query)
    if not query_tokens:
        fts_query = f'"{query}"'
    else:
        # FTS5 OR/MATCH 구문
        fts_query = " OR ".join(f'"{token}"' for token in query_tokens)
        
    sql = f"""
        SELECT chunk_id, dataset_type, source_id, content, bm25({FTS_TABLE_NAME}) AS rank_score
        FROM {FTS_TABLE_NAME}
        WHERE tokens MATCH ?
    """
    params: List[Any] = [fts_query]
    
    if dataset_type:
        sql += " AND dataset_type = ?"
        params.append(dataset_type)
        
    # SQLite BM25 점수는 음수(작을수록 정밀함)로 나오므로 ABS/정규화
    sql += f" ORDER BY rank_score ASC LIMIT ?"
    params.append(top_k)
    
    start_time = time.time()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    elapsed_ms = (time.time() - start_time) * 1000
    
    results = []
    for row in rows:
        # BM25 rank score를 0.0 ~ 1.0 양수 유사도로 변환
        raw_bm25 = abs(float(row["rank_score"]))
        norm_score = round(1.0 / (1.0 + raw_bm25), 4)
        
        results.append({
            "chunk_id": int(row["chunk_id"]),
            "dataset_type": row["dataset_type"],
            "source_id": int(row["source_id"]),
            "content": row["content"],
            "score": norm_score,
            "raw_bm25": raw_bm25,
            "latency_ms": elapsed_ms
        })
        
    return results
