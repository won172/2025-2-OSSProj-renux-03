"""SQLite 기반 Entity-Relation 지식 그래프 파이프라인 (Phase 2 - Task 2.1)

학과, 교과목, 학칙 규정, 교직원, 학사일정을 노드(Entity)와 간선(Relation)으로 묶어
옵시디언 방식의 네트워크 관계 탐색(Bi-directional Graph Query)을 제공합니다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.database import DATABASE_FILE

logger = logging.getLogger(__name__)

ENTITY_TABLE = "entities"
RELATION_TABLE = "entity_relations"


def get_graph_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or DATABASE_FILE
    conn = sqlite3.connect(str(target_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_graph_tables(conn: sqlite3.Connection) -> None:
    """엔티티 및 관계 스키마 테이블 생성"""
    cursor = conn.cursor()
    
    # 1. 엔티티 테이블 (노드)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {ENTITY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT UNIQUE NOT NULL,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            attributes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_entity_type ON {ENTITY_TABLE}(entity_type);")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_entity_name ON {ENTITY_TABLE}(name);")

    # 2. 엔티티 관계 테이블 (간선)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {RELATION_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            description TEXT,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_entity_id) REFERENCES {ENTITY_TABLE}(entity_id),
            FOREIGN KEY (target_entity_id) REFERENCES {ENTITY_TABLE}(entity_id)
        );
    """)
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_relation_src ON {RELATION_TABLE}(source_entity_id);")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_relation_tgt ON {RELATION_TABLE}(target_entity_id);")
    cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_relation_type ON {RELATION_TABLE}(relation_type);")
    
    conn.commit()


def populate_entity_graph(conn: sqlite3.Connection) -> Tuple[int, int]:
    """기존 SQLite 데이터(courses, rules, staff, schedule)에서 지식 노드와 관계를 추출 및 색인"""
    cursor = conn.cursor()
    
    # 기존 그래프 데이터 리셋 (재현성 확보)
    cursor.execute(f"DELETE FROM {RELATION_TABLE}")
    cursor.execute(f"DELETE FROM {ENTITY_TABLE}")
    conn.commit()

    entities_batch: List[Tuple[str, str, str, str, str]] = []
    relations_batch: List[Tuple[str, str, str, str, str]] = []
    
    seen_entities = set()

    def add_entity(entity_id: str, entity_type: str, name: str, desc: str = "", attrs: Optional[Dict] = None):
        if entity_id not in seen_entities:
            seen_entities.add(entity_id)
            entities_batch.append((
                entity_id,
                entity_type,
                name,
                desc,
                json.dumps(attrs or {}, ensure_ascii=False)
            ))

    def add_relation(src: str, rel_type: str, tgt: str, desc: str = "", meta: Optional[Dict] = None):
        relations_batch.append((
            src,
            rel_type,
            tgt,
            desc,
            json.dumps(meta or {}, ensure_ascii=False)
        ))

    # --- A. 학과 및 교과목 엔티티/관계 파싱 ---
    cursor.execute("SELECT id, course_code, title, description, raw_data FROM courses")
    course_rows = cursor.fetchall()
    
    for row in course_rows:
        course_id = f"course:{row['course_code'] or row['id']}"
        title = row["title"] or f"교과목-{row['id']}"
        desc = row["description"] or ""
        
        attrs = {}
        if row["raw_data"]:
            try:
                attrs = json.loads(row["raw_data"])
            except Exception:
                pass
                
        dept_name = attrs.get("department_name") or attrs.get("department") or "전공공통"
        college_name = attrs.get("college_name") or attrs.get("college") or "동국대학교"
        
        dept_id = f"dept:{dept_name}"
        college_id = f"college:{college_name}"

        add_entity(college_id, "college", college_name)
        add_entity(dept_id, "department", dept_name)
        add_entity(course_id, "course", title, desc, attrs)

        # 관계 설정: (단과대) -[HAS_DEPARTMENT]-> (학과) -[HAS_CURRICULUM]-> (교과목)
        add_relation(college_id, "HAS_DEPARTMENT", dept_id)
        add_relation(dept_id, "HAS_CURRICULUM", course_id, f"{dept_name} 교육과정 포함 과목")

    # --- B. 교직원 연락처 엔티티/관계 파싱 ---
    cursor.execute("SELECT id, department, name, position, role, phone, email FROM staff")
    staff_rows = cursor.fetchall()

    for row in staff_rows:
        staff_id = f"staff:{row['id']}"
        staff_name = row["name"] or "교직원"
        dept_name = row["department"] or "행정부서"
        dept_id = f"dept:{dept_name}"

        attrs = {
            "position": row["position"],
            "role": row["role"],
            "phone": row["phone"],
            "email": row["email"]
        }

        add_entity(dept_id, "department", dept_name)
        add_entity(staff_id, "staff", staff_name, row["role"] or "", attrs)
        add_relation(dept_id, "MANAGED_BY_STAFF", staff_id, f"{dept_name} 담당 직원")

    # --- C. 학칙/규정 엔티티/관계 파싱 ---
    cursor.execute("SELECT id, filename, relative_dir, title, full_text FROM rules")
    rule_rows = cursor.fetchall()

    for row in rule_rows:
        rule_title = row["title"] or row["filename"] or f"규정-{row['id']}"
        rule_id = f"rule:{row['id']}"
        category = row["relative_dir"] or "학칙"

        attrs = {"category": category, "filename": row["filename"]}
        add_entity(rule_id, "rule", rule_title, (row["full_text"] or "")[:200], attrs)

        # 주요 키워드(학사경고, 장학금, 수강신청 등)에 따른 연동 노드 자동 생성
        if "학사경고" in (row["full_text"] or ""):
            academic_warning_id = "concept:학사경고"
            add_entity(academic_warning_id, "concept", "학사경고 조항", "학칙상 성적 미달 시 제재 규정")
            add_relation(rule_id, "REGULATES", academic_warning_id, "학사경고 세부 규칙 명시")

        if "장학" in rule_title or "장학" in (row["full_text"] or ""):
            scholarship_id = "concept:장학금"
            add_entity(scholarship_id, "concept", "장학금 지급 규정", "장학금 신청 자격 및 선발 기준")
            add_relation(rule_id, "REGULATES", scholarship_id, "장학금 규정 관련 조항")

    # --- DB Batch Insert ---
    cursor.executemany(
        f"INSERT OR REPLACE INTO {ENTITY_TABLE} (entity_id, entity_type, name, description, attributes) VALUES (?, ?, ?, ?, ?)",
        entities_batch
    )
    cursor.executemany(
        f"INSERT INTO {RELATION_TABLE} (source_entity_id, relation_type, target_entity_id, description, metadata) VALUES (?, ?, ?, ?, ?)",
        relations_batch
    )
    conn.commit()

    logger.info(f"✅ 엔티티 그래프 구축 완료! (노드 {len(entities_batch)}개, 간선 {len(relations_batch)}개)")
    return len(entities_batch), len(relations_batch)


def search_entity_graph(
    conn: sqlite3.Connection,
    keyword: str,
    max_hops: int = 2
) -> Dict[str, Any]:
    """키워드에 매칭되는 엔티티 노드와 연결된 옵시디언 서브그래프(1~2 Hops) 탐색

    Returns:
        {
            "nodes": [{"id": ..., "name": ..., "type": ...}],
            "edges": [{"source": ..., "target": ..., "relation": ...}]
        }
    """
    cursor = conn.cursor()
    
    # 1. 키워드 매칭 초기 엔티티 찾기
    cursor.execute(f"""
        SELECT entity_id, entity_type, name, description, attributes
        FROM {ENTITY_TABLE}
        WHERE name LIKE ? OR entity_id LIKE ? OR description LIKE ?
        LIMIT 10
    """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"))
    
    initial_rows = cursor.fetchall()
    if not initial_rows:
        return {"nodes": [], "edges": []}

    nodes_dict: Dict[str, Dict] = {}
    edges_list: List[Dict] = []
    visited_entities = set()

    queue = [row["entity_id"] for row in initial_rows]
    for row in initial_rows:
        eid = row["entity_id"]
        nodes_dict[eid] = {
            "id": eid,
            "name": row["name"],
            "type": row["entity_type"],
            "description": row["description"],
        }
        visited_entities.add(eid)

    # 2. 1-Hop / 2-Hop 양방향 간선 탐색 (Obsidian Graph View 스타일)
    for hop in range(max_hops):
        if not queue:
            break
        current_batch = list(queue)
        queue.clear()

        placeholders = ",".join("?" for _ in current_batch)
        sql = f"""
            SELECT source_entity_id, relation_type, target_entity_id, description
            FROM {RELATION_TABLE}
            WHERE source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders})
        """
        params = current_batch + current_batch
        cursor.execute(sql, params)
        rel_rows = cursor.fetchall()

        for rel in rel_rows:
            src = rel["source_entity_id"]
            tgt = rel["target_entity_id"]
            rel_type = rel["relation_type"]

            edges_list.append({
                "source": src,
                "target": tgt,
                "relation": rel_type,
                "description": rel["description"]
            })

            for neighbor in (src, tgt):
                if neighbor not in visited_entities:
                    visited_entities.add(neighbor)
                    queue.append(neighbor)
                    
                    # 노드 상세 정보 추가
                    cursor.execute(f"SELECT entity_id, entity_type, name, description FROM {ENTITY_TABLE} WHERE entity_id = ?", (neighbor,))
                    n_row = cursor.fetchone()
                    if n_row:
                        nodes_dict[neighbor] = {
                            "id": n_row["entity_id"],
                            "name": n_row["name"],
                            "type": n_row["entity_type"],
                            "description": n_row["description"]
                        }

    return {
        "nodes": list(nodes_dict.values()),
        "edges": edges_list
    }
