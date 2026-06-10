"""메시지 이력을 유지하며 답변을 생성하는 헬퍼입니다.

답변 생성 프로바이더는 LLM_PROVIDER 설정으로 OpenAI와 로컬(Ollama) 사이에서
전환할 수 있습니다. 두 프로바이더 모두 LangChain 채팅 인터페이스를 사용하므로
스트리밍/비스트리밍 코드 경로는 동일합니다.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import redis
from dotenv import load_dotenv
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_openai import ChatOpenAI
from langchain_redis import RedisChatMessageHistory

from src.config import (
    LLM_FALLBACK_ENABLED,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_MODEL,
    OLLAMA_CHAT_TEMPERATURE,
    OLLAMA_TIMEOUT_SECONDS,
    OPENAI_CHAT_MAX_RETRIES,
    OPENAI_CHAT_MODEL,
    OPENAI_CHAT_TEMPERATURE,
    OPENAI_CHAT_TIMEOUT_SECONDS,
    REDIS_HISTORY_TTL_SECONDS,
    REDIS_URL,
)

load_dotenv()

# Redis 클라이언트를 미리 초기화하여 RedisChatMessageHistory에 전달합니다.
_REDIS_CLIENT = redis.from_url(REDIS_URL)

# 로거 설정
logger = logging.getLogger(__name__)


def _build_openai_llm() -> BaseChatModel:
    return ChatOpenAI(
        model=OPENAI_CHAT_MODEL,
        temperature=OPENAI_CHAT_TEMPERATURE,
        timeout=OPENAI_CHAT_TIMEOUT_SECONDS,
        max_retries=OPENAI_CHAT_MAX_RETRIES,
    )


def _build_ollama_llm() -> BaseChatModel:
    # langchain_ollama 는 선택적 의존성이므로 필요한 시점에만 import 한다.
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=OLLAMA_CHAT_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=OLLAMA_CHAT_TEMPERATURE,
        client_kwargs={"timeout": OLLAMA_TIMEOUT_SECONDS},
    )


_PROVIDER_BUILDERS = {
    "openai": _build_openai_llm,
    "ollama": _build_ollama_llm,
}


@lru_cache(maxsize=2)
def _get_chat_llm(provider: str) -> BaseChatModel:
    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        logger.warning("Unknown LLM_PROVIDER '%s', falling back to openai.", provider)
        builder = _build_openai_llm
    return builder()


def _primary_provider() -> str:
    return LLM_PROVIDER if LLM_PROVIDER in _PROVIDER_BUILDERS else "openai"


def _fallback_provider(primary: str) -> str | None:
    if not LLM_FALLBACK_ENABLED:
        return None
    return "ollama" if primary == "openai" else "openai"


# Redis 가용성 결과를 짧게 캐시해 매 요청마다 ping() 하지 않도록 한다.
_REDIS_HEALTH_TTL_SECONDS = 30.0
_redis_health = {"ok": False, "checked_at": 0.0}


def _redis_available() -> bool:
    now = time.monotonic()
    if now - _redis_health["checked_at"] < _REDIS_HEALTH_TTL_SECONDS:
        return _redis_health["ok"]
    try:
        _REDIS_CLIENT.ping()
        _redis_health["ok"] = True
    except Exception as e:
        logger.warning("Redis unavailable, falling back to in-memory history: %s", e)
        _redis_health["ok"] = False
    _redis_health["checked_at"] = now
    return _redis_health["ok"]


def _get_session_history(session_id: str) -> BaseChatMessageHistory:
    if _redis_available():
        try:
            return RedisChatMessageHistory(
                f"dongttok:chat_history:{session_id}",
                redis_client=_REDIS_CLIENT,
                ttl=REDIS_HISTORY_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("Redis history init failed, falling back to ChatMessageHistory: %s", e)
            _redis_health["ok"] = False  # 다음 요청에서 재확인하도록 무효화
    return ChatMessageHistory()

@lru_cache(maxsize=2)
def _get_system_prompt(mode: str = "rag") -> str:
    return """
당신은 동국대학교 AI 어시스턴트 '동똑이'입니다. 다른 수식어나 전문 분야를 언급하지 마세요.
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


def _is_valid_message(message: BaseMessage) -> bool:
    content = getattr(message, "content", None)
    return isinstance(content, str) and bool(content.strip()) and isinstance(
        message, (HumanMessage, AIMessage, SystemMessage)
    )


def _build_messages(
    question: str, context: str, history: BaseChatMessageHistory, current_date: str
) -> list[BaseMessage]:
    """시스템 프롬프트 + 이전 대화 이력 + 현재 질문을 LangChain 메시지로 구성합니다."""
    messages: list[BaseMessage] = [
        SystemMessage(content=_get_system_prompt("rag").format(current_date=current_date))
    ]
    messages.extend(m for m in history.messages if _is_valid_message(m))
    messages.append(
        HumanMessage(
            content=_build_user_prompt(
                question=question,
                context=context or "컨텍스트가 제공되지 않았습니다.",
                mode="rag",
            )
        )
    )
    return messages


def _extract_text(content) -> str:
    return content.strip() if isinstance(content, str) else str(content).strip()


async def _invoke_with_provider(provider: str, messages: list[BaseMessage]) -> str:
    llm = _get_chat_llm(provider)
    response = await llm.ainvoke(messages)
    answer = _extract_text(response.content)
    if not answer:
        raise RuntimeError(f"LLM provider '{provider}' returned an empty response.")
    return answer


async def generate_langchain_answer(question: str, context: str, session_id: str | None = None, current_date: str = "") -> str:
    """선택된 프로바이더로 답변을 생성합니다. 실패 시 반대 프로바이더로 폴백합니다."""
    actual_session_id = session_id or "default_session"
    primary = _primary_provider()
    logger.info("Generating answer for session_id=%s (provider=%s)", actual_session_id, primary)

    history = _get_session_history(actual_session_id)
    messages = _build_messages(question, context, history, current_date)

    try:
        answer = await _invoke_with_provider(primary, messages)
    except Exception as exc:
        fallback = _fallback_provider(primary)
        if fallback is None:
            raise
        logger.warning("Provider '%s' failed (%s); falling back to '%s'.", primary, exc, fallback)
        answer = await _invoke_with_provider(fallback, messages)

    history.add_user_message(question)
    history.add_ai_message(answer)
    return answer


async def generate_langchain_answer_stream(question: str, context: str, session_id: str | None = None, current_date: str = ""):
    """선택된 프로바이더로 답변을 스트리밍 생성합니다.

    스트리밍은 토큰이 이미 전송되기 시작하면 중간 폴백이 불가능하므로, 첫 토큰을
    받기 전 단계의 실패에 한해 반대 프로바이더로 1회 폴백합니다.
    """
    actual_session_id = session_id or "default_session"
    primary = _primary_provider()
    logger.info("Generating streaming answer for session_id=%s (provider=%s)", actual_session_id, primary)

    history = _get_session_history(actual_session_id)
    messages = _build_messages(question, context, history, current_date)

    async def _stream(provider: str):
        llm = _get_chat_llm(provider)
        async for chunk in llm.astream(messages):
            text = _extract_text(chunk.content)
            if text:
                yield text

    full_answer: list[str] = []
    started = False
    try:
        async for text in _stream(primary):
            started = True
            full_answer.append(text)
            yield text
    except Exception as exc:
        fallback = _fallback_provider(primary)
        if started or fallback is None:
            raise
        logger.warning("Streaming provider '%s' failed before first token (%s); falling back to '%s'.", primary, exc, fallback)
        async for text in _stream(fallback):
            full_answer.append(text)
            yield text

    answer_text = "".join(full_answer)
    # 토큰이 하나도 생성되지 않았다면 빈 AI 메시지를 이력에 남기지 않는다.
    if not answer_text.strip():
        logger.warning(
            "Empty streaming answer; skipping history for session_id=%s", actual_session_id
        )
        return
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
