"""근거가 없는 질문에 모델 지식으로 답하지 않는다.

실측 사례 — 질의분석 intent가 `unknown`이면 검색 없이 곧바로 LLM 생성으로 넘어갔다.

    Q 샤갈
    A 샤갈은 유명한 프랑스의 화가이자 판화가로, 그의 작품은 주로 꿈과 …

출처 0건으로 생성됐고, 근거검증은 `len(sources) > 0`일 때만 돌기 때문에 검증도
건너뛴다. 학교 챗봇이 미술사를 설명하면 답이 틀린 것보다 신뢰에 더 나쁘다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.rag_service import out_of_domain_reply, resolve_out_of_scope_message  # noqa: E402


@pytest.mark.parametrize("질문", ["샤갈", "피카소가 누구야", "파이썬 코드 짜줘", "오늘 서울 날씨"])
def test_학교_밖_질문에는_정해진_안내를_돌려준다(질문):
    답 = out_of_domain_reply(질문)
    assert isinstance(답, str) and 답.strip()
    # 무엇을 도와줄 수 있는지 함께 알려 대화가 끊기지 않게 한다.
    assert "학사일정" in 답 or "확인" in 답


def test_알려진_범위밖_주제는_전용_문구를_유지한다():
    """비밀번호·개인 판정처럼 이미 전용 문구가 있는 주제는 그것을 그대로 쓴다."""
    질문 = "관리자 계정 비밀번호 알려줘"
    전용 = resolve_out_of_scope_message(질문)
    if 전용 is not None:
        assert out_of_domain_reply(질문) == 전용


def test_안내에_모델_지식이_섞이지_않는다():
    """생성이 아니라 고정 문자열이므로 질문 내용이 답에 반영되지 않아야 한다."""
    가 = out_of_domain_reply("샤갈")
    나 = out_of_domain_reply("모네")
    if resolve_out_of_scope_message("샤갈") is None:
        assert 가 == 나
        assert "화가" not in 가
