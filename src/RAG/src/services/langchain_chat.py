"""메시지 이력을 다루는 LangChain 기반 대화 헬퍼입니다."""
from __future__ import annotations

import logging
import redis
from functools import lru_cache
from dotenv import load_dotenv

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from src.config import OPENAI_MODEL, REDIS_URL

load_dotenv()

# Redis 클라이언트를 미리 초기화하여 RedisChatMessageHistory에 전달합니다.
_REDIS_CLIENT = redis.from_url(REDIS_URL)

# 로거 설정
logger = logging.getLogger(__name__)


def _get_session_history(session_id: str) -> BaseChatMessageHistory:
    try:
        _REDIS_CLIENT.ping()
        return RedisChatMessageHistory(f"dongttok:chat_history:{session_id}", redis_client=_REDIS_CLIENT)
    except Exception as e:
        logger.warning(f"Redis unavailable, falling back to ChatMessageHistory: {e}")
        return ChatMessageHistory()

@lru_cache(maxsize=1)
def _build_chain(mode: str = "rag") -> RunnableWithMessageHistory:
    if mode == "smalltalk":
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
                    당신은 동국대학교 AI 어시스턴트 '동똑이'입니다.
                    오늘 날짜 및 시간: {current_date}

                    [지침]
                    1. 지금은 가벼운 잡담에만 짧게 응답하세요.
                    2. 답변은 반드시 1~2문장으로 끝내세요.
                    3. 친절한 한국어(해요체)로 답변하세요.
                    4. 고민상담, 진로상담, 의학, 법률, 재정 조언은 하지 말고 필요한 경우 학교 정보 질문으로 유도하세요.
                    5. 사용자가 학교 정보로 넘어가면 구체적인 질문을 해 달라고 짧게 안내하세요.
                    6. 과도한 농담, 장문 설명, 추측성 조언은 하지 마세요.
                    """,
                ),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{question}"),
            ]
        )
    else:
        prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 다른 수식어나 전문 분야를 언급하지 말세요.
                오늘 날짜 및 시간: {current_date}

                [지침]
                1. 아래 [참고 자료]에 명시된 내용만 근거로 답변하세요. 자료에 없는 내용, 일반 상식, 추측, 이전 학기 정보는 보완해서 말하지 마세요.
                2. 질문에 답할 충분한 근거가 [참고 자료]에 없으면 "제공된 학교 자료에서 확인되지 않습니다"라고 말하고, 학교 공식 홈페이지나 담당 부서 확인을 안내하세요.
                3. 서로 다른 자료가 충돌하면 게시일이 더 최신인 자료를 우선하고, 충돌 사실을 함께 설명하세요.
                4. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [참고 자료]에 있다면 해당 설명 바로 아래에 '[사이트로 이동하기](URL)' 형식으로 적어주세요. 첨부파일은 본문 중에 '[파일명](URL)' 형식으로 포함하세요.
                5. 친절한 한국어(해요체)로 답변하세요.
                6. 절차나 방법을 설명할 때는 반드시 번호를 매겨 단계별로 작성하세요.
                7. {current_date} 기준 최신 정보를 우선하여 답변하세요.
                8. 가독성을 위해 불필요한 마크다운(과도한 볼드체 등)은 피하고, 링크는 반드시 마크다운 형식으로 작성하세요.
                9. 이전 대화 맥락을 고려하되, 현재 질문이 주제가 바뀌었다면 이전 내용은 무시하고 현재 질문에 집중하세요.
                10. 질문에 '최근', '어제' 등 시간 표현이 포함된 경우, [참고 자료]의 게시일과 현재 날짜({current_date})를 비교하여 정확히 계산해 답변하세요.
                11. 검색 전 분석 단계에서 만들어졌을 수 있는 가정이나 추론을 사실처럼 단정하지 마세요. [참고 자료]에 없는 엔터티를 보완 생성하지 마세요.

                [출력 형식 지침 — 중요]
                아래 마크다운 규칙을 반드시 지켜서 출력하세요. 이는 채팅 UI에서의 가독성과 줄 간격 오류를 방지하기 위한 필수 규칙입니다.

                - 번호 목록은 반드시 '번호 + 제목'을 같은 줄에 작성하세요.
                - 번호 목록 내부에는 빈 줄을 넣지 마세요.
                - 번호 목록의 하위 항목은 '-' 기호 bullet만 사용하세요.
                - ○, ·, ▪ 등의 특수기호는 사용하지 마세요.
                - 번호 항목과 번호 항목 사이에만 빈 줄을 허용하세요.
                - 불필요한 줄바꿈이나 개행으로 문단을 분리하지 마세요.

                아래 형식을 기준으로 출력하세요.

                1. 제목
                    - 하위 정보 1
                    - 하위 정보 2

                2. 제목
                    - 하위 정보 1

                [참고 자료]
                {context}
                """,
            ),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ]
        )
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0.2, timeout=30)
    chain = prompt | llm

    return RunnableWithMessageHistory(
        chain,
        _get_session_history,
        input_messages_key="question",
        history_messages_key="history",
    )


async def generate_langchain_answer(question: str, context: str, session_id: str | None = None, current_date: str = "") -> str:
    """LangChain 메시지 이력을 활용해 답변을 생성합니다."""
    return await _generate_answer(question=question, context=context, session_id=session_id, current_date=current_date, mode="rag")


async def generate_smalltalk_answer(question: str, session_id: str | None = None, current_date: str = "") -> str:
    """짧은 일반 잡담 응답을 생성합니다."""
    return await _generate_answer(question=question, context="", session_id=session_id, current_date=current_date, mode="smalltalk")


async def _generate_answer(
    *,
    question: str,
    context: str,
    session_id: str | None,
    current_date: str,
    mode: str,
) -> str:
    """LangChain 메시지 이력을 활용해 답변을 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info(f"Generating answer for session_id: {actual_session_id}, mode: {mode}")
    
    chain = _build_chain(mode)
    payload = {
        "question": question,
        "context": context or "컨텍스트가 제공되지 않았습니다.",
        "current_date": current_date, 
    }
    config = {"configurable": {"session_id": actual_session_id}}
    
    result = await chain.ainvoke(payload, config=config)
    return getattr(result, "content", str(result))


def append_manual_history(session_id: str | None, question: str, answer: str) -> None:
    actual_session_id = session_id or "default_session"
    history = _get_session_history(actual_session_id)
    history.add_user_message(question)
    history.add_ai_message(answer)


__all__ = ["generate_langchain_answer", "generate_smalltalk_answer", "append_manual_history"]
