"""LangChain-based conversational helper with message history."""
from __future__ import annotations

from functools import lru_cache
from typing import Dict

from dotenv import load_dotenv
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI

from src.config import OPENAI_MODEL

load_dotenv()

_HISTORY_STORE: Dict[str, InMemoryChatMessageHistory] = {}


def _get_history(session_id: str) -> InMemoryChatMessageHistory:
    if session_id not in _HISTORY_STORE:
        _HISTORY_STORE[session_id] = InMemoryChatMessageHistory()
    return _HISTORY_STORE[session_id]


@lru_cache(maxsize=1)
def _build_chain() -> RunnableWithMessageHistory:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "당신은 동국대학교 캠퍼스 어시스턴트입니다. 제공된 컨텍스트만 근거로 한국어로 답변하세요. 최근 날짜의 공지사항만 답변에 포함하세요."
                " 모르면 모른다고 답변하세요.\n\n[컨텍스트]\n{context}\n",
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
        _get_history,
        input_messages_key="question",
        history_messages_key="history",
    )


def generate_langchain_answer(question: str, context: str, session_id: str | None = None) -> str:
    """Return an answer using LangChain message history support."""
    chain = _build_chain()
    payload = {
        "question": question,
        "context": context or "컨텍스트가 제공되지 않았습니다.",
    }
    config = {"configurable": {"session_id": session_id or "default_session"}}
    return chain.invoke(payload, config=config)


__all__ = ["generate_langchain_answer"]
