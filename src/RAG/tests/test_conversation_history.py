"""History outage/recovery contract, using the installed Redis/LangChain codecs."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
import redis
from langchain_core.messages import AIMessage, HumanMessage, message_to_dict, messages_from_dict
from langchain_redis import RedisChatMessageHistory
from redisvl.index import SearchIndex

from src.services import conversation_history as memory
from src.services import langchain_chat as chat


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class RedisDouble:
    """Persist via the real message JSON codec; inject failures at I/O boundaries."""
    def __init__(self):
        self.mode = "healthy"
        self.data = {}
        self.errors = []

    def factory(self, session_id):
        if self.mode == "unavailable":
            return None
        if self.mode == "init":
            raise AttributeError("'dict' object has no attribute 'index'")
        outer = self
        records = self.data.setdefault(session_id, {})

        class Backend:
            @property
            def messages(self):
                if outer.mode == "read":
                    raise ConnectionError("read disconnected")
                ordered = sorted(records.values(), key=lambda v: v["data"]["additional_kwargs"][memory.MEMORY_TIME])
                return messages_from_dict(json.loads(json.dumps(ordered)))

            def add_messages(self, messages):
                if outer.mode == "write":
                    raise ConnectionError("write disconnected")
                for message in messages:
                    records[message.additional_kwargs[memory.MEMORY_ID]] = message_to_dict(message)
                    if outer.mode == "partial":
                        raise TimeoutError("legacy partial write")
                if outer.mode == "unknown":
                    raise TimeoutError("applied but reply lost")

        return Backend()


def pair(number=1):
    return [HumanMessage(content=f"질문 {number}"), AIMessage(content=f"답변 {number}")]


@pytest.mark.parametrize("failure", ["unavailable", "init", "read", "write", "partial", "unknown"])
def test_session_survives_failure_then_recovers_without_loss_or_duplicates(failure):
    backend, clock = RedisDouble(), Clock()
    store = memory.HistoryStore(backend.factory, backend.errors.append, ttl=100, clock=clock)
    store.get("a").add_messages(pair(1))
    backend.mode = failure
    clock.now += 1
    store.get("a").add_messages(pair(2))
    assert [m.content for m in store.get("a").messages] == ["질문 1", "답변 1", "질문 2", "답변 2"]
    assert store.get("b").messages == []
    backend.mode = "healthy"
    for _ in range(3):
        assert len(store.get("a").messages) == 4
    # Simulate a new worker/restart: recovered turns must really be in Redis.
    other_worker = memory.HistoryStore(backend.factory, backend.errors.append, ttl=100, clock=clock)
    assert [m.content for m in other_worker.get("a").messages] == ["질문 1", "답변 1", "질문 2", "답변 2"]
    assert len(backend.data["a"]) == 4
    assert len(backend.errors) > 0 if failure != "unavailable" else not backend.errors


def test_init_failure_from_first_turn_and_repeated_identical_turns_are_preserved():
    backend = RedisDouble()
    backend.mode = "init"
    store = memory.HistoryStore(backend.factory, backend.errors.append)
    for _ in range(2):
        store.get("a").add_messages(pair())
    assert len(store.get("a").messages) == 4
    backend.mode = "healthy"
    assert len(store.get("a").messages) == 4
    assert len(backend.data["a"]) == 4  # Deduplication is by event ID, not text.


def test_session_capacity_idle_expiry_message_bound_and_original_ttl():
    clock = Clock()
    store = memory.HistoryStore(lambda _s: None, lambda _e: None, ttl=5, clock=clock,
                                max_sessions=2, max_messages=4, idle_seconds=10)
    for n in range(3):
        store.get("a").add_messages(pair(n))
    assert len(store.get("a").messages) == 4
    store.get("b")
    store.get("c")
    assert store.get("a").messages == []  # LRU evicted only a's local buffer.
    store.get("a").add_messages(pair())
    clock.now += 6
    assert store.get("a").messages == []  # Reading must not refresh message TTL.
    store.get("a").add_messages(pair())
    clock.now += 11
    assert store.get("a").messages == []


def test_concurrent_completed_turns_remain_paired_and_chat_isolated():
    backend = RedisDouble()
    store = memory.HistoryStore(backend.factory, backend.errors.append, max_messages=80)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: store.get("a").add_messages(pair(n)), range(30)))
    messages = store.get("a").messages
    assert len(messages) == 60
    for question, answer in zip(messages[::2], messages[1::2]):
        assert question.content.split()[-1] == answer.content.split()[-1]
    assert store.get("b").messages == []


def make_adapter(monkeypatch, definition, *, exists=True):
    # Use the installed SearchIndex schema and LangChain constructor, mocking
    # only Redis I/O. Both real FT.INFO response shapes reproduce the diagnosis.
    client = redis.Redis(host="127.0.0.1", port=1)
    monkeypatch.setattr(client, "client_setinfo", lambda *_a: None)
    monkeypatch.setattr(SearchIndex, "exists", lambda _s: exists)
    monkeypatch.setattr(SearchIndex, "info", lambda _s: {"index_definition": definition})
    creates = []
    monkeypatch.setattr(SearchIndex, "create", lambda _s, **kw: creates.append(kw))
    adapter = memory.CompatibleRedisChatMessageHistory("dongttok:chat_history:a", redis_client=client, ttl=60)
    return adapter, creates


@pytest.mark.parametrize("definition", [
    {"key_type": "JSON", "prefixes": ["chat:"]},
    ["key_type", "JSON", "prefixes", ["chat:"], "default_score", "1"],
    {b"key_type": b"JSON", b"prefixes": [b"chat:"]},
])
def test_current_library_accepts_resp2_and_resp3_without_recreating_index(monkeypatch, definition):
    adapter, creates = make_adapter(monkeypatch, definition)
    assert isinstance(adapter, RedisChatMessageHistory)
    assert adapter.index.schema.index.prefix == "chat:"
    assert creates == []
    with pytest.raises(NotImplementedError):
        adapter.delete()


def test_missing_index_is_created_without_overwrite_but_conflict_is_never_replaced(monkeypatch):
    _, creates = make_adapter(monkeypatch, {}, exists=False)
    assert creates == [{"overwrite": False}]
    with pytest.raises(ValueError, match="incompatible prefix"):
        make_adapter(monkeypatch, {"prefixes": ["other:"]})


def test_legacy_json_and_structured_metadata_decode_with_bounded_recent_reads(monkeypatch):
    adapter, _ = make_adapter(monkeypatch, {"prefixes": ["chat:"]})
    captured = []
    def query(_index, value):
        captured.append(value)
        return [
            {"type": "ai", "$.data": json.dumps({"content": "답변", "additional_kwargs": {
                "source_cues": [{"document_key": "notices:1", "title": "공식 공지"}]
            }}), "timestamp": "1001", "$.message_id": "old-ai"},
            {"type": "human", "$.data": json.dumps({"content": "옛 질문", "additional_kwargs": {}}),
             "timestamp": "1000", "$.message_id": "old-user"},
        ]
    monkeypatch.setattr(SearchIndex, "query", query)
    messages = adapter.messages
    assert [m.content for m in messages] == ["옛 질문", "답변"]
    assert messages[1].additional_kwargs["source_cues"][0]["document_key"] == "notices:1"
    assert messages[0].id == "old-user"
    assert captured[0].params == {}
    assert captured[0].query._num == memory.MAX_MESSAGES


def test_atomic_idempotent_write_keeps_existing_key_schema_and_absolute_ttl(monkeypatch):
    adapter, _ = make_adapter(monkeypatch, {"prefixes": ["chat:"]})
    pipeline = MagicMock()
    pipeline.__enter__.return_value = pipeline
    monkeypatch.setattr(adapter.redis_client, "pipeline", lambda **kw: pipeline if kw == {"transaction": True} else pytest.fail("non-atomic write"))
    monkeypatch.setattr(memory.time, "time", lambda: 1020.0)
    messages = pair()
    for index, message in enumerate(messages):
        message.additional_kwargs.update({memory.MEMORY_TIME: 1000.0 + index, memory.MEMORY_ID: str(index)})
    adapter.add_messages(messages)
    adapter.add_messages(messages)  # A timeout retry must target the same keys.
    writes = pipeline.json.return_value.set.call_args_list
    assert [call.args[0] for call in writes] == ["chat:dongttok:chat_history:a:0", "chat:dongttok:chat_history:a:1"] * 2
    assert [call.args[1] for call in pipeline.pexpireat.call_args_list] == [1060000, 1061000] * 2
    assert writes[0].args[2]["data"]["id"] == "0"
    assert pipeline.execute.call_count == 2


def test_source_cues_survive_serialization_after_long_answer_and_more_than_three_turns(monkeypatch):
    backend = RedisDouble()
    store = memory.HistoryStore(backend.factory, backend.errors.append)
    monkeypatch.setattr(chat, "_HISTORY_STORE", store)
    source = {"source_ref": "sha256:fixture", "source": "notices", "title": "대학생 창업 경진대회",
              "chunk_id": "notices:42:chunk:7", "url": "https://www.dongguk.edu/notice/42",
              "snippet": "DO NOT PERSIST THIS EVIDENCE", "metadata": {"doc_id": "notices:42"}}
    chat.append_manual_history("a", "교내 공모전 알려줘", "앞부분" * 300 + "본문 뒤 출처", [source])
    for n in range(5):
        chat.append_manual_history("a", f"독립 질문 {n}", "다른 답변")
    text = chat.get_recent_history_text("a")
    assert "대학생 창업 경진대회" in text and "notices:42:chunk:7" in text
    assert "notices:42" in text and "sha256:fixture" in text
    assert "DO NOT PERSIST THIS EVIDENCE" not in json.dumps(backend.data)
    assert "본문 뒤 출처" not in text
    from src.services.conversation import rewrite_with_context
    rewritten = rewrite_with_context("아까 창업 경진대회 문서 다시 보여줘", text)
    assert "대학생 창업 경진대회" in rewritten
    assert "독립 질문" not in rewritten
    assert chat.get_recent_history_text("b") == ""


def test_missing_session_never_uses_shared_default_history(monkeypatch):
    monkeypatch.setattr(chat, "_get_session_history", lambda _s: pytest.fail("implicit shared session"))
    chat.append_manual_history(None, "질문", "답변")
    assert chat.get_recent_history_text(None) == ""
