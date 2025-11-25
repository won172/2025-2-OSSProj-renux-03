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


@lru_cache(maxsize=1)
def _build_chain() -> RunnableWithMessageHistory:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 동국대학교 캠퍼스 정보를 제공하는 친절하고 간결한 어시스턴트입니다. 제공된 [컨텍스트]를 바탕으로 사용자의 [질문]에 대해 핵심 정보를 명확하고 간단하게 요약하여 한국어로 답변하세요. 컨텍스트에 답변이 없으면 '정보를 찾을 수 없습니다.'라고만 답하세요.\n\n[컨텍스트]\n{context}\n",
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


def generate_langchain_answer(question: str, context: str, session_id: str | None = None) -> str:
    """LangChain 메시지 이력을 활용해 답변을 생성합니다."""
    chain = _build_chain()
    payload = {
        "question": question,
        "context": context or "컨텍스트가 제공되지 않았습니다.",
    }
    config = {"configurable": {"session_id": session_id or "default_session"}}
    return chain.invoke(payload, config=config)


__all__ = ["generate_langchain_answer"]



