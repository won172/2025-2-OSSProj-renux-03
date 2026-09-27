"""동국대 직원 연락처를 수집하는 크롤러입니다.

기존에는 Selenium으로 jstree를 펼쳐 테이블을 긁었으나, 페이지가 사용하는
공개 AJAX API(`/ajax/staff/dept/data`, `/ajax/staff/data/list`)를 직접 호출하는
방식으로 재작성했다. 브라우저/DOM 재렌더링 의존이 사라져 사이트 레이아웃
변경에 견고하고, 컬럼도 의미 있는 이름(성명/직위/담당업무/전화번호)으로 저장한다.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import requests

BASE_URL = "https://www.dongguk.edu"
DEPT_TREE_URL = f"{BASE_URL}/ajax/staff/dept/data"
STAFF_LIST_URL = f"{BASE_URL}/ajax/staff/data/list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DonggukStaffCrawler/2.0)",
    "X-Requested-With": "XMLHttpRequest",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
REQUEST_DELAY = 0.15
OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "dongguk_staff_contacts.csv"


def fetch_dept_tree(timeout: float = 30.0) -> List[Dict[str, Any]]:
    """부서 트리(jstree 데이터)를 가져옵니다. 각 노드: id/parent/text."""
    response = requests.post(DEPT_TREE_URL, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list) or not data:
        raise RuntimeError("부서 트리 응답 형식이 예상과 다릅니다.")
    return data


def fetch_staff_for_dept(dept_seq: str, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """특정 부서의 교직원 목록을 가져옵니다."""
    response = requests.post(
        STAFF_LIST_URL, headers=HEADERS, data={"dept_seq": dept_seq}, timeout=timeout
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def _build_dept_paths(tree: List[Dict[str, Any]]) -> Dict[str, str]:
    """노드 id → '상위 > 하위' 전체 경로 문자열 매핑을 만듭니다."""
    by_id = {str(node.get("id")): node for node in tree if node.get("id")}
    paths: Dict[str, str] = {}

    def path_of(node_id: str) -> str:
        if node_id in paths:
            return paths[node_id]
        node = by_id.get(node_id)
        if node is None:
            return ""
        text = (node.get("text") or "").strip()
        parent = str(node.get("parent") or "")
        if parent in ("", "#", "Top") or parent not in by_id:
            paths[node_id] = text
        else:
            parent_path = path_of(parent)
            paths[node_id] = f"{parent_path} > {text}" if parent_path else text
        return paths[node_id]

    for node_id in by_id:
        path_of(node_id)
    return paths


def crawl_staff_contacts(
    delay: float = REQUEST_DELAY,
    *,
    request_timeout: float = 30.0,
) -> pd.DataFrame:
    tree = fetch_dept_tree(timeout=request_timeout)
    paths = _build_dept_paths(tree)

    dept_nodes = [
        node for node in tree
        if node.get("id") and str(node.get("id")) != "Top" and (node.get("text") or "").strip()
    ]
    print(f"🌳 부서 {len(dept_nodes)}개 발견. 교직원 수집 시작...")

    records: List[Dict[str, str]] = []
    failed = 0
    failed_departments: List[str] = []
    fetched_departments = 0
    for node in dept_nodes:
        dept_seq = str(node["id"])
        dept_name = (node.get("text") or "").strip()
        try:
            rows = fetch_staff_for_dept(dept_seq, timeout=request_timeout)
        except Exception as exc:  # noqa: BLE001 — 한 부서 실패가 전체 수집을 막지 않도록
            failed += 1
            failed_departments.append(dept_seq)
            print(f"⚠️ 부서 '{dept_name}'({dept_seq}) 수집 실패: {exc}")
            continue
        fetched_departments += 1

        for row in rows:
            name = (row.get("staff_name") or "").strip()
            position = (row.get("staff_pos") or "").strip()
            charge = (row.get("charge") or "").strip()
            telephone = (row.get("telephone") or "").strip()
            if not any((name, position, charge, telephone)):
                continue
            records.append(
                {
                    # 공개 API가 안정 식별자를 제공하는 환경에서는 연락처가 바뀌어도
                    # 동일 인물로 연결한다. 키가 없는 구버전 응답은 빈 값으로 유지한다.
                    "원천ID": str(
                        row.get("staff_seq")
                        or row.get("staff_id")
                        or row.get("user_id")
                        or row.get("emp_no")
                        or row.get("seq")
                        or ""
                    ).strip(),
                    "조직(트리)": (row.get("dept_name") or dept_name).strip(),
                    "부서경로": paths.get(dept_seq, dept_name),
                    "성명": name,
                    "직위": position,
                    "담당업무": charge,
                    "전화번호": telephone,
                }
            )

        if delay:
            time.sleep(delay)

    if failed:
        print(f"⚠️ 부서 단위 수집 실패 {failed}건")

    df = pd.DataFrame(records)
    if not df.empty:
        df.drop_duplicates(inplace=True)
    df.attrs["crawl_diagnostics"] = {
        "requested_departments": len(dept_nodes),
        "fetched_departments": fetched_departments,
        "failed_departments": failed,
        "failed_department_ids": failed_departments,
    }
    print(f"✅ 교직원 {len(df)}건 수집 완료.")
    return df


def main() -> None:
    df = crawl_staff_contacts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"총 {len(df)}건의 데이터 저장 완료! ({OUTPUT_PATH})")


__all__ = ["crawl_staff_contacts", "fetch_dept_tree", "fetch_staff_for_dept"]


if __name__ == "__main__":
    main()
