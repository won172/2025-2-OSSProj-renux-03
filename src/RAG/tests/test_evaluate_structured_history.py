from __future__ import annotations

from datetime import datetime

from scripts.evaluate_structured_history import select_structured_history
from src.database import RagQueryLog


def _log(log_id: int, question: str, route: str, request_id: str = "real"):
    return RagQueryLog(
        id=log_id, question=question, route=route, request_id=request_id,
        created_at=datetime(2026, 8, 27, 9), as_of="2026-08-27",
    )


def test_history_selection_preserves_single_route_and_real_traffic_boundary():
    cases, selection = select_structured_history([
        _log(1, "장학팀 연락처 알려줘", '["staff"]'),
        _log(2, "장학팀 연락처 알려줘", '["staff"]'),
        _log(3, "장학팀 연락처 알려줘", '["staff", "notices"]'),
        _log(4, "CSC2007 어느 학과?", '["courses"]', "eval_synthetic"),
        _log(5, "자료구조 설명해줘", '["courses"]'),
    ])

    assert len(cases) == 1
    assert cases[0].dataset == "staff"
    assert cases[0].occurrences == 2
    assert selection["single_route_logs"] == 4
    assert selection["eligible_structured_cases"] == 1
