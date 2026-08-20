

# --- is_closed 표기 통일 -------------------------------------------------------


def test_원천마다_다른_휴무_표기를_모두_읽는다():
    """같은 값을 두 곳이 다르게 해석하고 있었다.

    CSV는 "True"/"False", 청크 아티팩트는 "1"/"0"으로 담는다(ingest가
    astype(str)로 저장). 그런데 이 함수는 "true"만 받았고 ingest.py는
    {"true","1","1.0"}을 썼다. 아티팩트의 "1"을 못 읽으면 휴무가 통째로
    운영으로 뒤집힌다 — 실제 데이터에서 57건이 그 상태였다.
    """
    from src.utils.briefing import is_closed_row

    for 참 in ("True", "true", "1", "1.0"):
        assert is_closed_row(참, "아무 메뉴") is True, f"{참!r}를 휴무로 읽지 못했다"
    for 거짓 in ("False", "false", "0", ""):
        assert is_closed_row(거짓, "삼겹살김치철판 6000원") is False, f"{거짓!r}를 운영으로 읽지 못했다"


def test_플래그가_없어도_본문이_휴무면_쉬는_것으로_본다():
    """아티팩트 본문은 완전일치가 아니라 문장 형태다."""
    from src.utils.briefing import CLOSED_MENU_TEXT, is_closed_row

    assert is_closed_row("", CLOSED_MENU_TEXT) is True
    assert is_closed_row("", "상록원2층식당(백반·일품)는 2026-08-03(월)에 휴무입니다.") is True
    # 메뉴 안의 "방중 휴무"(석식 주석)는 식당 전체 휴무가 아니다.
    assert is_closed_row("0", "중식 낙삼불고기덮밥 ￦7,000 방중 휴무 석식 에그카레") is False
