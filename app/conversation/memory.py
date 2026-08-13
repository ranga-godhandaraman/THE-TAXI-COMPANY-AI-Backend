"""In-process conversation memory (POC). Replace with Redis/DB later."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy

from app.conversation.models import ConversationState

_lock = threading.Lock()
_STORE: dict[str, ConversationState] = {}


def new_conversation_id() -> str:
    return str(uuid.uuid4())


def get_state(conversation_id: str) -> ConversationState:
    with _lock:
        state = _STORE.get(conversation_id)
        if state is None:
            return ConversationState()
        return deepcopy(state)


def save_state(conversation_id: str, state: ConversationState) -> None:
    with _lock:
        _STORE[conversation_id] = deepcopy(state)


def clear_state(conversation_id: str) -> None:
    with _lock:
        _STORE.pop(conversation_id, None)


def reset_all() -> None:
    with _lock:
        _STORE.clear()
