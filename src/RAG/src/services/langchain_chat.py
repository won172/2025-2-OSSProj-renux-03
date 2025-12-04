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
                "당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 오늘 날짜: {current_date}\n\n[지침]\n1. [컨텍스트] 내용만으로 답변하세요. 없는 정보는 지어내지 마세요.\n2. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [컨텍스트]에 있다면 해당 설명 바로 아래에 \"URL: (링크주소)\" 형식으로 적어주세요. 절대 마크다운 링크([텍스트](URL))로 변환하지 말고 주소만 그대로 쓰세요. 주소가 없다면 URL에 대해 쓰지 마세요.\n3. 친절한 한국어(해요체)로 답변하세요.\n4. 절차나 방법은 번호를 매겨 단계별로 설명하세요.\n5. 정보가 없으면 정중히 사과하고 재검색을 유도하세요.\n6. {current_date} 기준 최신 정보를 우선하세요.\n7. 답변에 볼드체(**) 등 마크다운 서식을 절대 사용하지 마세요.\n8. 이전 대화 맥락을 고려하되, 현재 질문이 주제가 바뀌었다면 이전 내용은 무시하고 현재 질문에 집중하세요.\n9. 질문에 '최근', '어제' 등 시간 표현이 있다면, 제공된 [컨텍스트] 내 문서의 '게시일'과 현재 날짜({current_date})를 비교하여 정확히 계산하고 답변하세요.\n\n[컨텍스트]\n{context}",
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



