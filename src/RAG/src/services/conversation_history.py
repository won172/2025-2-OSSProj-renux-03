"""Bounded session memory with non-destructive, idempotent Redis recovery.

Redis keeps the existing chat: JSON schema, index, session namespace and per-message
TTL. The local buffer is process-local: restart, idle expiry or capacity eviction
can lose unsynchronised turns. It is an outage buffer, not a durable database.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from threading import RLock

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, messages_from_dict
from langchain_redis import RedisChatMessageHistory
from redisvl.index import SearchIndex
from redisvl.query import FilterQuery
from redisvl.query.filter import Tag

logger = logging.getLogger(__name__)
MAX_MESSAGES = 40  # 20 completed turns, shared by Redis reads and local memory
MAX_CONTENT_CHARS = 12000
MEMORY_ID = "dongttok_message_id"
MEMORY_TIME = "dongttok_created_at"


def _decoded(value):
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, dict):
        return {_decoded(k): _decoded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decoded(v) for v in value]
    return value


class CompatibleRedisChatMessageHistory(RedisChatMessageHistory):
    """Handle RESP2/RESP3 FT.INFO without monkeypatching or overwriting indexes."""

    def _create_search_index(self) -> None:
        if self.overwrite_index:
            raise ValueError("History index overwrite is not supported")
        self.index = SearchIndex.from_dict({
            "index": {"name": self.index_name, "prefix": self.key_prefix, "storage_type": "json"},
            "fields": [
                {"name": "session_id", "type": "tag", "path": "$.session_id"},
                {"name": "content", "type": "text", "path": "$.data.content"},
                {"name": "type", "type": "tag", "path": "$.type"},
                {"name": "timestamp", "type": "numeric", "path": "$.timestamp"},
            ],
        }, redis_client=self.redis_client)
        # exists() distinguishes absence from connection/permission errors. Never
        # catch an arbitrary INFO error and interpret it as permission to rebuild.
        if self.index.exists():
            info = _decoded(self.index.info())
            definition = info.get("index_definition", {})
            if isinstance(definition, list):
                definition = dict(zip(definition[::2], definition[1::2]))
            prefixes = definition.get("prefixes", [])
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            if self.key_prefix not in prefixes:
                raise ValueError("Existing history index has an incompatible prefix")
        else:
            self.index.create(overwrite=False)

    def _build_message_entry(self, message: BaseMessage):
        message.additional_kwargs.setdefault(MEMORY_ID, uuid.uuid4().hex)
        message.additional_kwargs.setdefault(MEMORY_TIME, time.time())
        data, _ = super()._build_message_entry(message)
        # Stable IDs/timestamps survive a write timeout after Redis applied it.
        # Retrying JSON.SET addresses the same key rather than duplicating a turn.
        message_id = message.additional_kwargs[MEMORY_ID]
        data["message_id"] = message_id
        data["timestamp"] = message.additional_kwargs[MEMORY_TIME]
        data["data"]["id"] = message_id
        return data, self._message_key(message_id)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        # A completed human/AI pair is visible atomically. Absolute expiry also
        # prevents outage recovery or a retry from extending the original TTL.
        with self.redis_client.pipeline(transaction=True) as pipeline:
            for message in messages:
                data, key = self._build_message_entry(message)
                expires = None if self.ttl is None else int((data["timestamp"] + self.ttl) * 1000)
                if expires is not None and expires <= time.time() * 1000:
                    continue
                pipeline.json().set(key, "$", data)
                if expires is not None:
                    pipeline.pexpireat(key, expires)
            pipeline.execute()

    def add_message(self, message: BaseMessage) -> None:
        self.add_messages([message])

    @property
    def messages(self) -> list[BaseMessage]:
        query = FilterQuery(
            filter_expression=Tag("session_id") == self.session_id,
            return_fields=["type", "$.data", "timestamp", "$.message_id"],
            num_results=MAX_MESSAGES,
        ).sort_by("timestamp", asc=False)
        result = []
        for row in reversed(self.index.query(query)):
            data = json.loads(row["$.data"])
            extra = data.setdefault("additional_kwargs", {})
            extra.setdefault(MEMORY_TIME, float(row["timestamp"]))
            message_id = row.get("$.message_id") or data.get("id")
            if message_id:
                extra.setdefault(MEMORY_ID, message_id)
                data.setdefault("id", message_id)
            result.extend(messages_from_dict([{"type": row["type"], "data": data}]))
        return result

    def clear(self) -> None:
        raise NotImplementedError("History deletion requires a separate authorised workflow")

    def delete(self) -> None:
        raise NotImplementedError("History index deletion is disabled")


class SessionHistory(BaseChatMessageHistory):
    def __init__(self, session_id, backend_factory, on_failure, ttl, max_messages, clock):
        self.session_id = session_id
        self._backend_factory = backend_factory
        self._on_failure = on_failure
        self._ttl = ttl
        self._max_messages = max_messages
        self._clock = clock
        self._local: list[BaseMessage] = []
        self._pending: list[BaseMessage] = []
        self._lock = RLock()
        self.touched_at = clock()

    def _trim(self):
        now = self._clock()
        self.touched_at = now
        self._local = [m for m in self._local if not self._ttl or
                       now - m.additional_kwargs.get(MEMORY_TIME, now) < self._ttl][-self._max_messages:]
        retained = {m.additional_kwargs.get(MEMORY_ID) for m in self._local}
        self._pending = [m for m in self._pending if m.additional_kwargs[MEMORY_ID] in retained]

    def _sync(self):
        self._trim()
        try:
            backend = self._backend_factory(self.session_id)
            if backend is None:
                return
            remote = backend.messages  # A failed read must not discard the local buffer.
            merged = {m.additional_kwargs.get(MEMORY_ID) or f"legacy:{i}": m
                      for i, m in enumerate(remote)}
            for message in self._local:
                # Only keep locally confirmed messages until their original TTL;
                # pending writes always retain their original chronological position.
                key = message.additional_kwargs.get(MEMORY_ID)
                if key:
                    merged.setdefault(key, message)
            self._local = sorted(merged.values(), key=lambda m: m.additional_kwargs.get(MEMORY_TIME, 0))
            self._trim()
            if self._pending:
                backend.add_messages(self._pending)
                self._pending = []  # Only clear after confirmed success.
        except Exception as exc:  # History failures must not fail answer generation.
            self._on_failure(exc)

    @property
    def messages(self) -> list[BaseMessage]:
        with self._lock:
            self._sync()
            return list(self._local)

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        with self._lock:
            for message in messages:
                if not isinstance(message.content, str) or not message.content.strip():
                    continue
                extra = dict(message.additional_kwargs)
                extra.setdefault(MEMORY_ID, uuid.uuid4().hex)
                # Strict order for human/AI pairs even with a coarse test clock.
                previous = self._local[-1].additional_kwargs.get(MEMORY_TIME, 0) if self._local else 0
                extra.setdefault(MEMORY_TIME, max(self._clock(), previous + 0.000001))
                bounded = message.model_copy(update={
                    "content": message.content[:MAX_CONTENT_CHARS], "additional_kwargs": extra,
                    "id": extra[MEMORY_ID],
                })
                self._local.append(bounded)
                self._pending.append(bounded)
            self._sync()

    def add_message(self, message: BaseMessage) -> None:
        self.add_messages([message])

    def clear(self) -> None:
        raise NotImplementedError("History deletion requires a separate authorised workflow")


class HistoryStore:
    """LRU bounded, idle-expiring registry; locking is per session for I/O."""

    def __init__(self, backend_factory: Callable, on_failure: Callable, *, ttl=None,
                 max_sessions=1000, max_messages=MAX_MESSAGES, idle_seconds=3600,
                 clock=time.time):
        self.backend_factory, self.on_failure, self.ttl = backend_factory, on_failure, ttl
        self.max_sessions, self.max_messages = max(1, max_sessions), max(2, max_messages)
        self.idle_seconds, self.clock = idle_seconds, clock
        self._sessions: OrderedDict[str, SessionHistory] = OrderedDict()
        self._lock = RLock()

    def get(self, session_id: str) -> SessionHistory:
        if not session_id:
            raise ValueError("A session ID is required")
        with self._lock:
            now = self.clock()
            for key in list(self._sessions):
                if now - self._sessions[key].touched_at >= self.idle_seconds:
                    self._sessions.pop(key)
            history = self._sessions.pop(session_id, None)
            if history is None:
                history = SessionHistory(session_id, self.backend_factory, self.on_failure,
                                         self.ttl, self.max_messages, self.clock)
            history.touched_at = now
            self._sessions[session_id] = history
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return history
