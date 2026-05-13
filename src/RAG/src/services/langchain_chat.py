"""메시지 이력을 유지하며 Ollama 채팅 API로 답변을 생성하는 헬퍼입니다."""
from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache

import httpx
import redis
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_redis import RedisChatMessageHistory

from src.config import (
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_CONNECT_TIMEOUT_SECONDS,
    OLLAMA_REQUEST_RETRIES,
    OLLAMA_TIMEOUT_SECONDS,
    REDIS_URL,
)

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

@lru_cache(maxsize=2)
def _get_system_prompt(mode: str = "rag") -> str:
    return """
당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 다른 수식어나 전문 분야를 언급하지 말세요.
오늘 날짜 및 시간: {current_date}

[지침]
1. 사용자가 인사, 이름, 너의 정체, 대화 자체를 묻는 경우에는 [참고 자료]와 무관하게 자연스럽게 답하세요. 이름을 물으면 "동똑이"라고 답하세요.
2. 학교 정보, 공지, 학사, 장학, 수업, 교직원, 규정, 일정 질문은 아래 [참고 자료]에 명시된 내용만 근거로 답변하세요. 자료에 없는 내용, 일반 상식, 추측, 이전 학기 정보는 보완해서 말하지 마세요.
3. 학교 정보 질문에 답할 충분한 근거가 [참고 자료]에 없으면 "제공된 학교 자료에서 확인되지 않습니다"라고 말하고, 학교 공식 홈페이지나 담당 부서 확인을 안내하세요.
4. 서로 다른 자료가 충돌하면 게시일이 더 최신인 자료를 우선하고, 충돌 사실을 함께 설명하세요.
5. 답변에서 특정 정보를 언급할 때, 그 정보의 출처 URL이 [참고 자료]에 있다면 해당 설명 바로 아래에 '[사이트로 이동하기](URL)' 형식으로 적어주세요. 첨부파일은 본문 중에 '[파일명](URL)' 형식으로 포함하세요.
6. 친절한 한국어(해요체)로 답변하세요.
7. 절차나 방법을 설명할 때는 반드시 번호를 매겨 단계별로 작성하세요.
8. {current_date} 기준 최신 정보를 우선하여 답변하세요.
9. 가독성을 위해 불필요한 마크다운(과도한 볼드체 등)은 피하고, 링크는 반드시 마크다운 형식으로 작성하세요.
10. 이전 대화 맥락을 고려하되, 현재 질문이 주제가 바뀌었다면 이전 내용은 무시하고 현재 질문에 집중하세요.
11. 질문에 '최근', '어제' 등 시간 표현이 포함된 경우, [참고 자료]의 게시일과 현재 날짜({current_date})를 비교하여 정확히 계산해 답변하세요.
12. 검색 전 분석 단계에서 만들어졌을 수 있는 가정이나 추론을 사실처럼 단정하지 마세요. [참고 자료]에 없는 엔터티를 보완 생성하지 마세요.

[출력 형식 지침 — 중요]
- 번호 목록은 반드시 '번호 + 제목'을 같은 줄에 작성하세요.
- 번호 목록 내부에는 빈 줄을 넣지 마세요.
- 번호 목록의 하위 항목은 '-' 기호 bullet만 사용하세요.
- ○, ·, ▪ 등의 특수기호는 사용하지 마세요.
- 번호 항목과 번호 항목 사이에만 빈 줄을 허용하세요.
- 불필요한 줄바꿈이나 개행으로 문단을 분리하지 마세요.
""".strip()


def _build_user_prompt(question: str, context: str, mode: str) -> str:
    return f"[참고 자료]\n{context}\n\n[사용자 질문]\n{question}"


def _serialize_message(message: BaseMessage) -> dict[str, str] | None:
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None

    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, SystemMessage):
        role = "system"
    else:
        return None

    return {"role": role, "content": content}


def _create_ollama_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=OLLAMA_REQUEST_RETRIES,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _chat_with_ollama(messages: list[dict[str, str]]) -> str:
    session = _create_ollama_session()
    try:
        response = session.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                },
            },
            timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
    except requests.exceptions.ReadTimeout as exc:
        logger.error("Ollama request read timed out after %s seconds", OLLAMA_TIMEOUT_SECONDS, exc_info=exc)
        raise RuntimeError("Ollama request timed out. Please try again later.") from exc
    except requests.exceptions.RequestException as exc:
        logger.error("Ollama request failed", exc_info=exc)
        raise RuntimeError("Ollama request failed. Please check Ollama availability.") from exc

    payload = response.json()
    message = payload.get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama returned an empty response.")
    return content.strip()


async def _chat_with_ollama_stream(messages: list[dict[str, str]]):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=OLLAMA_CONNECT_TIMEOUT_SECONDS,
            read=OLLAMA_TIMEOUT_SECONDS,
            write=OLLAMA_CONNECT_TIMEOUT_SECONDS,
        )
    ) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            json={
                "model": OLLAMA_CHAT_MODEL,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": 0.2,
                },
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    content = payload.get("message", {}).get("content")
                    if content:
                        yield content
                    if payload.get("done"):
                        break
                except json.JSONDecodeError:
                    continue

async def generate_langchain_answer(question: str, context: str, session_id: str | None = None, current_date: str = "") -> str:
    """LangChain 메시지 이력을 활용해 답변을 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info(f"Generating answer for session_id: {actual_session_id}")

    history = _get_session_history(actual_session_id)
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _get_system_prompt("rag").format(current_date=current_date),
        }
    ]
    for message in history.messages:
        serialized = _serialize_message(message)
        if serialized is not None:
            messages.append(serialized)
    messages.append(
        {
            "role": "user",
            "content": _build_user_prompt(
                question=question,
                context=context or "컨텍스트가 제공되지 않았습니다.",
                mode="rag",
            ),
        }
    )

    answer = await asyncio.to_thread(_chat_with_ollama, messages)
    history.add_user_message(question)
    history.add_ai_message(answer)
    return answer

async def generate_langchain_answer_stream(question: str, context: str, session_id: str | None = None, current_date: str = ""):
    """LangChain 메시지 이력을 활용해 답변을 스트리밍으로 생성합니다."""
    actual_session_id = session_id or "default_session"
    logger.info(f"Generating streaming answer for session_id: {actual_session_id}")

    history = _get_session_history(actual_session_id)
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _get_system_prompt("rag").format(current_date=current_date),
        }
    ]
    for message in history.messages:
        serialized = _serialize_message(message)
        if serialized is not None:
            messages.append(serialized)
    messages.append(
        {
            "role": "user",
            "content": _build_user_prompt(
                question=question,
                context=context or "컨텍스트가 제공되지 않았습니다.",
                mode="rag",
            ),
        }
    )

    full_answer = []
    async for chunk in _chat_with_ollama_stream(messages):
        full_answer.append(chunk)
        yield chunk
    
    answer_text = "".join(full_answer)
    history.add_user_message(question)
    history.add_ai_message(answer_text)


def append_manual_history(session_id: str | None, question: str, answer: str) -> None:
    actual_session_id = session_id or "default_session"
    history = _get_session_history(actual_session_id)
    history.add_user_message(question)
    history.add_ai_message(answer)

__all__ = [
    "generate_langchain_answer",
    "generate_langchain_answer_stream",
    "append_manual_history",
]
