"""홈 브리핑용 원문 파싱.

rag_service 본체는 임베딩·벡터스토어를 함께 끌어오므로, 서비스 기동 없이
검증할 수 있는 순수 파싱 로직은 여기로 분리한다.
"""
from __future__ import annotations

CLOSED_MENU_TEXT = "휴무"


def split_meal_corners(menu_text: str, limit: int) -> list[dict[str, str]]:
    """학식 원문을 코너 단위로 쪼갠다.

    원문은 한 셀에 코너·가격·메뉴가 슬래시로 이어 붙어 있다:
        "[일반(뚝배기)] (뚝)우삼겹된장찌개 / [중식 A코너 6,500원] 쌀밥 ... / [석식] ..."

    대괄호로 시작하지 않는 조각은 앞 코너 메뉴의 연속이므로 이어 붙인다
    (원문에 코너 표기 없이 메뉴만 한 번 더 나오는 경우가 있다).
    """
    corners: list[dict[str, str]] = []
    if limit <= 0:
        return corners

    for segment in menu_text.split("/"):
        text = segment.strip()
        if not text:
            continue

        if text.startswith("[") and "]" in text:
            closing = text.index("]")
            corner = text[1:closing].strip()
            menu = text[closing + 1:].strip()
        else:
            if corners:
                corners[-1]["menu"] = f"{corners[-1]['menu']} · {text}".strip(" ·")
            continue

        if not menu:
            continue
        corners.append({"corner": corner, "menu": menu})
        if len(corners) >= limit:
            break

    return corners


def is_closed_row(is_closed_value: object, menu_text: str) -> bool:
    """식당이 오늘 쉬는지 판정한다.

    플래그는 원천마다 표기가 다르다. CSV는 "True"/"False", 청크 아티팩트는
    "1"/"0"으로 담는다(ingest가 astype(str)로 저장한다). bool() 변환에 의존할 수
    없고("False"도 참이 된다), 한쪽 표기만 받으면 다른 원천에서 휴무가 통째로
    운영으로 뒤집힌다 — 실제로 아티팩트의 "1"을 못 읽어 휴무 57건이 전부 운영으로
    판정됐다. ingest가 쓰는 집합과 같게 맞춘다.

    플래그가 비어 있어도 본문이 휴무를 뜻하면 쉬는 것으로 본다. 아티팩트 본문은
    "…는 2026-08-03(월)에 휴무입니다."처럼 문장이라 완전일치로는 걸리지 않는다.
    """
    if str(is_closed_value).strip().lower() in {"true", "1", "1.0"}:
        return True
    body = menu_text.strip()
    return body == CLOSED_MENU_TEXT or body.endswith("휴무입니다.")


def format_schedule_period(start_date: str | None, end_date: str | None) -> str:
    """학사일정 기간 표기. 하루짜리 일정은 날짜를 한 번만 쓴다."""
    start = (start_date or "").strip()
    end = (end_date or "").strip()
    if not start:
        return end
    if not end or end == start:
        return start
    return f"{start} ~ {end}"


__all__ = ["CLOSED_MENU_TEXT", "format_schedule_period", "is_closed_row", "split_meal_corners"]
