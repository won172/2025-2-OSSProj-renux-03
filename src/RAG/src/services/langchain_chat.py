"""메시지 이력을 다루는 LangChain 기반 대화 헬퍼입니다."""
from __future__ import annotations

from functools import lru_cache

import redis
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from src.config import OPENAI_MODEL, REDIS_URL

load_dotenv()

# Redis 클라이언트를 미리 초기화하여 RedisChatMessageHistory에 전달합니다.
# 이렇게 하면 RedisChatMessageHistory가 내부적으로 Redis.from_url을 호출할 때 발생하는
# 'TypeError: Redis.from_url() got multiple values for argument 'url'' 오류를 방지할 수 있습니다.
_REDIS_CLIENT = redis.from_url(REDIS_URL)


import logging

# 로거 설정
logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _build_chain() -> RunnableWithMessageHistory:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 다른 수식어나 전문 분야를 언급하지 말세요.
                오늘 날짜 및 시간: {current_date}

                [지침]
                1. 아래 [참고 자료]를 바탕으로 질문에 대해 자연스럽게 요약하여 답변하세요. 자료에 없는 내용은 지어내지 마세요.
                2. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [참고 자료]에 있다면 해당 설명 바로 아래에 '[사이트로 이동하기](URL)' 형식으로 적어주세요. 첨부파일은 본문 중에 '[파일명](URL)' 형식으로 포함하세요.
                3. 친절한 한국어(해요체)로 답변하세요.
                4. 절차나 방법을 설명할 때는 반드시 번호를 매겨 단계별로 작성하세요.
                5. 관련 정보가 없으면 정중히 사과하고, 재검색이나 추가 확인이 필요하다고 안내하세요.
                6. {current_date} 기준 최신 정보를 우선하여 답변하세요.
                7. 가독성을 위해 불필요한 마크다운(과도한 볼드체 등)은 피하고, 링크는 반드시 마크다운 형식으로 작성하세요.
                8. 이전 대화 맥락을 고려하되, 현재 질문이 주제가 바뀌었다면 이전 내용은 무시하고 현재 질문에 집중하세요.
                9. 질문에 '최근', '어제' 등 시간 표현이 포함된 경우, [참고 자료]의 게시일과 현재 날짜({current_date})를 비교하여 정확히 계산해 답변하세요.

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
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0.2)
    parser = StrOutputParser()
    chain = prompt | llm | parser

    return RunnableWithMessageHistory(
        chain,
        lambda session_id: RedisChatMessageHistory(session_id, redis_client=_REDIS_CLIENT),
        input_messages_key="question",
        history_messages_key="history",
    )


async def generate_langchain_answer(question: str, context: str, session_id: str | None = None, current_date: str = "") -> str:
    """LangChain 메시지 이력을 활용해 답변을 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info(f"Generating answer for session_id: {actual_session_id}")
    
    chain = _build_chain()
    payload = {
        "question": question,
        "context": context or "컨텍스트가 제공되지 않았습니다.",
        "current_date": current_date, 
    }
    config = {"configurable": {"session_id": actual_session_id}}
    
    # 디버깅: 현재 세션의 히스토리 조회 시도 (Redis 직접 확인)
    try:
        history_instance = RedisChatMessageHistory(actual_session_id, redis_client=_REDIS_CLIENT)
        messages = history_instance.messages
        logger.info(f"Current history for session {actual_session_id}: {len(messages)} messages found.")
    except Exception as e:
        logger.error(f"Failed to fetch history for debug: {e}")

    return await chain.ainvoke(payload, config=config)


__all__ = ["generate_langchain_answer"]



