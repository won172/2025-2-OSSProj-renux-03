"""질문에 등장한 학과명을 색인된 `major` 값으로 해석한다.

교과목 질의에서 학과는 유사도 신호가 아니라 **범위 제약**이다. 그런데 학과명은
표기 변이가 심해 유사도로는 안정적으로 걸리지 않는다. 실측한 실패:

    "컴퓨터·AI학부 교과과정"   → 컴퓨터·AI학부      (정확 표기라 우연히 성공)
    "컴퓨터AI학부 교과과정"    → 경영정보학과       ✗
    "컴퓨터 AI학부 전공과목"   → 정보통신공학전공    ✗

가운뎃점 하나 차이로 다른 학과가 나온다. 학교 원문은 `컴퓨터·AI학부`처럼 구분기호를
쓰지만 학생은 그렇게 입력하지 않는다.

대조는 **원문 위치에서 경계를 보며** 한다. 구분기호를 지운 키로 단순 포함 검사를 하면
"수학과목"이 `수학과`를, "화학과제"가 `화학과`를 잘못 잡는다(실측 확인).
그래서 학과명의 구분기호 자리만 유연하게 두고, 앞뒤가 다른 한글로 이어지면 제외한다.

한 학부에 전공이 여러 개면(`영어영문학부` → 영어문학전공·영어통번역학전공) 그 전공들을
모두 돌려준다 — 학부 이름만 말한 학생은 그 학부 전체를 묻는 것이기 때문이다.
"""
from __future__ import annotations

import csv
import functools
import re
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from src.config import DATA_DIR, DATA_SOURCES

# 학과명 안에서 표기가 흔들리는 구분기호. 이 자리만 유연하게 대조한다.
_SEPARATOR_CLASS = r"[\s·∙‧•/\-]*"
_SEPARATOR_RUN = re.compile(r"[^0-9A-Za-z가-힣]+")
# 학과명이 더 긴 한글 낱말의 일부로 걸리는 것을 막는 경계.
_LEFT_BOUNDARY = r"(?<![가-힣A-Za-z])"

# 한국어 조사·서술격은 명사에 띄어쓰기 없이 붙는다. 오른쪽 경계를 `(?![가-힣])`로만
# 두면 "화학과의", "학부가", "학과에서"가 전부 인식에 실패한다. 실측: 조사 25개 전부
# 실패했고 실제 로그 16건(폴백 3건)이 여기 걸렸다.
#
# 그렇다고 경계를 없애면 "수학과목"이 "수학과"로 잡힌다. 그래서 **알려진 조사만**
# 선택적으로 흘려보내고, 그 뒤에 다시 한글이 오면 거부한다. "목"은 조사가 아니므로
# "수학과목"은 그대로 걸러진다.
_PARTICLES = (
    # 격조사·보조사
    "으로부터", "에게서", "한테서", "으로서", "으로써", "이라도", "이라는", "이든지",
    "로부터", "에게", "한테", "께서", "으로", "로서", "로써", "라도", "라는", "든지",
    "이나", "이랑", "조차", "마저", "밖에", "처럼", "보다", "마다", "에서", "까지",
    "부터", "하고", "이란",
    "께", "와", "과", "랑", "도", "만", "나", "란", "의", "에", "로",
    "은", "는", "이", "가", "을", "를",
    # 축약형. "에선"은 "에서는"이 줄어든 것이라 조사를 겹쳐도 만들어지지 않는다.
    "에선", "에겐", "한텐", "으론", "론", "껜",
    # 서술격조사·복수
    "이었", "이에요", "입니다", "인데", "이야", "이고", "이며", "예요", "이다",
    "야", "들",
)
# 정규식 교체는 왼쪽부터 시도하므로 긴 것을 앞에 둔다(백트래킹 비용도 줄인다).
_PARTICLE_ALTERNATION = "|".join(
    re.escape(p) for p in sorted(_PARTICLES, key=len, reverse=True)
)
# 조사는 겹쳐 붙는다("학과에서는", "학과와는", "학과들도"). 두 번까지 허용한다.
# 무제한으로 두면 조사 아닌 한글까지 흘려보낼 여지가 커진다.
_RIGHT_BOUNDARY = rf"(?:{_PARTICLE_ALTERNATION}){{0,2}}(?![가-힣])"


def _department_pattern(name: str) -> re.Pattern[str] | None:
    """학과명을 표기 변이에 견디는 정규식으로 바꾼다."""
    parts = [re.escape(part) for part in _SEPARATOR_RUN.split(name) if part]
    if not parts:
        return None
    body = _SEPARATOR_CLASS.join(parts)
    return re.compile(f"{_LEFT_BOUNDARY}{body}{_RIGHT_BOUNDARY}", re.IGNORECASE)


def _iter_source_departments() -> List[str]:
    """색인 정본(SourceDocument)에서 학과명을 읽는다.

    테스트와 명시적 마이그레이션 도구가 넘긴 별도 경로만 CSV를 사용한다.
    운영 기본 경로는 과정 색인과 동일한 canonical payload를 읽어야 한다.
    """
    path = DATA_SOURCES.get("courses_all")
    if path is None:
        return []

    default_path = (DATA_DIR / "dongguk_courses_all.csv").resolve()
    if Path(path).resolve() == default_path:
        try:
            from src.database import SessionLocal
            from src.pipelines.ingest import load_canonical_source_frame

            session = SessionLocal()
            try:
                frame = load_canonical_source_frame(session, "courses")
            finally:
                session.close()
            if frame.empty:
                return []
            column = "department_name" if "department_name" in frame.columns else "major"
            if column not in frame.columns:
                return []
            return [
                str(value).strip()
                for value in frame[column]
                if str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
            ]
        except Exception:
            return []

    # Explicit non-default paths are retained for isolated tests and one-time
    # legacy migration diagnostics.
    if not path.exists():
        return []
    try:
        # 학과명 컬럼만 읽는다 — 전체를 읽으면 수 MB를 불필요하게 파싱한다.
        frame = pd.read_csv(path, usecols=["department_name"]).fillna("")
    except (ValueError, OSError):
        return []
    return [str(value).strip() for value in frame["department_name"] if str(value).strip()]


def _iter_aliases() -> List[Tuple[str, str]]:
    """(사용자 표현, 표준 학과명) 쌍. 파일이 없으면 빈 목록."""
    path = DATA_SOURCES.get("courses_all")
    if path is None:
        return []
    alias_path = path.with_name("dongguk_department_aliases.csv")
    if not alias_path.exists():
        return []
    pairs: List[Tuple[str, str]] = []
    try:
        with alias_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                alias = str(row.get("alias", "")).strip()
                canonical = str(row.get("canonical_department_name", "")).strip()
                if alias and canonical:
                    pairs.append((alias, canonical))
    except OSError:
        return []
    return pairs


def _compact(text: str) -> str:
    return _SEPARATOR_RUN.sub("", str(text or "")).lower()


@functools.lru_cache(maxsize=1)
def _department_entries() -> Tuple[Tuple[str, re.Pattern[str], Tuple[str, ...]], ...]:
    """(표기, 정규식, 색인에 존재하는 학과명들)을 긴 표기 우선으로 정렬해 돌려준다."""
    known = _iter_source_departments()
    known_by_compact: Dict[str, str] = {_compact(name): name for name in known}

    surfaces: Dict[str, List[str]] = {}

    def add(surface: str, canonical: str) -> None:
        surface = surface.strip()
        if not surface or len(_compact(surface)) < 2:
            return
        bucket = surfaces.setdefault(surface, [])
        if canonical not in bucket:
            bucket.append(canonical)

    for name in known:
        add(name, name)
        # "영어영문학부 영어문학전공"처럼 학부+전공 형태면 학부 이름만으로도 찾히게 한다.
        for separator in (" ", "/"):
            head = name.split(separator, 1)[0].strip()
            if head and head != name:
                add(head, name)

    # 별칭은 표준명이 실제 색인에 있을 때만 등록한다. 색인에 없는 이름으로 이어지는
    # 별칭은 필터를 빈 결과로 만들 뿐이라 무시하는 편이 안전하다.
    for alias, canonical in _iter_aliases():
        canonical_compact = _compact(canonical)
        resolved = known_by_compact.get(canonical_compact)
        if resolved is not None:
            add(alias, resolved)
            continue
        # 표준명이 학부 이름이고 색인에는 전공 단위로만 있는 경우를 구제한다.
        for compact_key, name in known_by_compact.items():
            if compact_key.startswith(canonical_compact) and len(canonical_compact) >= 3:
                add(alias, name)

    entries = []
    for surface, canonicals in surfaces.items():
        pattern = _department_pattern(surface)
        if pattern is not None:
            entries.append((surface, pattern, tuple(canonicals)))
    # 긴 표기를 먼저 대조해야 "컴퓨터·AI학부"가 "AI학부"보다 우선한다.
    entries.sort(key=lambda item: len(_compact(item[0])), reverse=True)
    return tuple(entries)


def resolve_departments(query: str) -> Tuple[str, ...]:
    """질문에 등장한 학과를 색인된 `major` 값으로 해석한다(없으면 빈 튜플)."""
    if not query:
        return ()
    for _surface, pattern, canonicals in _department_entries():
        if pattern.search(query):
            return canonicals
    return ()


__all__ = ["resolve_departments"]
