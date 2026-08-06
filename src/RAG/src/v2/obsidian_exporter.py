"""Obsidian Markdown Vault Exporter (Phase 2 - Task 2.2)

SQLite에 저장된 엔티티 및 지식 그래프(entities, entity_relations, rules, courses, staff)를
옵시디언(Obsidian) 양방향 위키링크([[문서명#조항]]) 스타일의 마크다운 지식 보관소(Vault)로 자동 내보냅니다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.database import DATABASE_FILE

logger = logging.getLogger(__name__)

DEFAULT_VAULT_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "obsidian_vault"


def export_obsidian_vault(
    db_path: Optional[Path] = None,
    vault_dir: Optional[Path] = None
) -> Dict[str, int]:
    """SQLite 지식 DB ➔ Obsidian Vault Markdown 파일 세트 자동 내보내기

    Returns:
        {"departments": count, "courses": count, "rules": count, "staff": count, "concepts": count}
    """
    target_db = db_path or DATABASE_FILE
    target_vault = vault_dir or DEFAULT_VAULT_DIR
    target_vault.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 하위 디렉터리 구조 생성
    depts_dir = target_vault / "Departments"
    courses_dir = target_vault / "Courses"
    rules_dir = target_vault / "Rules"
    staff_dir = target_vault / "Staff"
    concepts_dir = target_vault / "Concepts"

    for d in (depts_dir, courses_dir, rules_dir, staff_dir, concepts_dir):
        d.mkdir(parents=True, exist_ok=True)

    stats = {"departments": 0, "courses": 0, "rules": 0, "staff": 0, "concepts": 0}
    start_time = time.time()

    # 1. 학과 (Departments) 내보내기
    cursor.execute("""
        SELECT e.entity_id, e.name, e.attributes
        FROM entities e
        WHERE e.entity_type = 'department'
    """)
    dept_rows = cursor.fetchall()
    
    for d_row in dept_rows:
        dept_name = d_row["name"].strip()
        if not dept_name:
            continue
            
        dept_id = d_row["entity_id"]
        
        # 연결된 과목 찾기
        cursor.execute("""
            SELECT r.target_entity_id, e.name AS course_name, e.description
            FROM entity_relations r
            JOIN entities e ON r.target_entity_id = e.entity_id
            WHERE r.source_entity_id = ? AND r.relation_type = 'HAS_CURRICULUM'
        """, (dept_id,))
        course_links = cursor.fetchall()

        # 연결된 교직원 찾기
        cursor.execute("""
            SELECT r.target_entity_id, e.name AS staff_name, e.description
            FROM entity_relations r
            JOIN entities e ON r.target_entity_id = e.entity_id
            WHERE r.source_entity_id = ? AND r.relation_type = 'MANAGED_BY_STAFF'
        """, (dept_id,))
        staff_links = cursor.fetchall()

        # 마크다운 내용 구성
        md_content = f"""---
type: department
name: "{dept_name}"
tags:
  - 동국대학교
  - 학과정보
  - 교육과정
---

# 🏛️ {dept_name}

> 동국대학교 {dept_name} 학사 및 개설 교육과정 안내 문서입니다.

## 📚 개설 교과목 (Curriculum)
"""
        if course_links:
            for c in course_links[:30]:
                c_name = c["course_name"].strip()
                md_content += f"* [[{c_name}]]\n"
        else:
            md_content += "* 등록된 교과목 정보가 없습니다.\n"

        md_content += "\n## 👤 학과 담당 교직원 (Staff)\n"
        if staff_links:
            for s in staff_links[:15]:
                s_name = s["staff_name"].strip()
                md_content += f"* [[{s_name}]] - {s['description'] or '담당업무'}\n"
        else:
            md_content += "* 등록된 교직원 연락처가 없습니다.\n"

        safe_filename = dept_name.replace("/", "_") + ".md"
        with open(depts_dir / safe_filename, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        stats["departments"] += 1

    # 2. 교과목 (Courses) 내보내기
    cursor.execute("""
        SELECT e.entity_id, e.name, e.description, e.attributes
        FROM entities e
        WHERE e.entity_type = 'course'
    """)
    course_rows = cursor.fetchall()

    for c_row in course_rows:
        c_name = c_row["name"].strip()
        if not c_name:
            continue
            
        c_id = c_row["entity_id"]
        c_desc = c_row["description"] or "교과목 개요 정보가 준비 중입니다."
        attrs = json.loads(c_row["attributes"] or "{}")

        dept_name = attrs.get("department_name") or attrs.get("department") or "전공공통"
        code = attrs.get("course_code") or c_id.replace("course:", "")

        md_content = f"""---
type: course
code: "{code}"
name: "{c_name}"
department: "[[{dept_name}]]"
tags:
  - 교과목
  - 전공
---

# 📖 {c_name} (`{code}`)

* **개설 학과**: [[{dept_name}]]

## 📝 교과목 개요
{c_desc}

## 🔗 연관 학과 및 규정
* 소속 학과: [[{dept_name}]]
"""
        safe_filename = (c_name + f"_{code}").replace("/", "_")[:50] + ".md"
        with open(courses_dir / safe_filename, "w", encoding="utf-8") as f:
            f.write(md_content)

        stats["courses"] += 1

    # 3. 학칙/규정 (Rules) 내보내기
    cursor.execute("""
        SELECT id, filename, relative_dir, title, full_text
        FROM rules
    """)
    rule_rows = cursor.fetchall()

    for r_row in rule_rows:
        r_title = (r_row["title"] or r_row["filename"] or f"규정-{r_row['id']}").strip()
        cat = r_row["relative_dir"] or "학칙"
        body = r_row["full_text"] or ""

        md_content = f"""---
type: rule
category: "{cat}"
title: "{r_title}"
tags:
  - 학칙
  - 규정
---

# 📜 {r_title}

* **분류**: {cat}
* **파일명**: `{r_row['filename']}`

## 📑 규정 원문 내용
```text
{body[:2500]}
```

## 🔗 연관 개념 (Concepts)
* [[학사경고]]
* [[장학금]]
"""
        safe_filename = r_title.replace("/", "_")[:60] + ".md"
        with open(rules_dir / safe_filename, "w", encoding="utf-8") as f:
            f.write(md_content)

        stats["rules"] += 1

    # 4. 주요 개념 (Concepts) 내보내기
    concepts = [
        ("학사경고", "성적 미달 시 발생하는 학칙 제재 조치 및 이수 조건"),
        ("장학금", "성적우수, 형설, 복지 등 동국대학교 장학금 수혜 규정 및 조건"),
        ("수강신청", "학기별 교과목 수강신청, 수강정정 및 취소 일정/절차"),
        ("졸업요건", "단과대학 및 학과별 이수학점, 논문, 외국어 기준 조건")
    ]

    for concept_name, concept_desc in concepts:
        md_content = f"""---
type: concept
name: "{concept_name}"
tags:
  - 주요개념
  - 학사행정
---

# 💡 {concept_name}

> {concept_desc}

## 📜 연관 규정 문서 (Rules)
* [[3-6-12. 장학금 지급에 관한 시행세칙(2024.6.27.).hwp]]
* [[2026학번 학적 및 학생 관련]]

## 🏛️ 관련 주요 학과 (Departments)
* [[경영학과]]
* [[통계학과]]
"""
        with open(concepts_dir / f"{concept_name}.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        stats["concepts"] += 1

    # 5. 공지사항 (Notices) 내보내기
    notices_dir = target_vault / "Notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    stats["notices"] = 0

    cursor.execute("""
        SELECT id, board, title, category, published_date, content
        FROM notices
        LIMIT 1000
    """)
    notice_rows = cursor.fetchall()

    for n_row in notice_rows:
        n_title = (n_row["title"] or f"공지-{n_row['id']}").strip()
        board = n_row["board"] or "일반공지"
        pub_date = n_row["published_date"] or ""
        body = n_row["content"] or ""

        md_content = f"""---
type: notice
board: "{board}"
date: "{pub_date}"
title: "{n_title}"
tags:
  - 공지사항
  - 학사공지
---

# 📢 {n_title}

* **게시판**: `{board}`
* **게시일자**: `{pub_date}`

## 📝 공지사항 본문 내용
{body}

## 🔗 연관 개념 & 학과
* [[학사경고]]
* [[장학금]]
* [[수강신청]]
"""
        safe_filename = n_title.replace("/", "_")[:60] + ".md"
        with open(notices_dir / safe_filename, "w", encoding="utf-8") as f:
            f.write(md_content)

        stats["notices"] += 1

    elapsed = time.time() - start_time
    logger.info(f"✅ Obsidian Vault 내보내기 완료! ({target_vault}, {elapsed:.2f}초 소요)")
    return stats
