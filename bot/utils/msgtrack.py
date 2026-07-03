"""Учёт ID сообщений пользователей в памяти — чтобы при бане/ограничении можно
было удалить всю его недавнюю переписку (не только последнее сообщение).

Хранится ограниченное число последних сообщений на пользователя в чате.
"""
from collections import defaultdict, deque

_MAX_PER_USER = 300
_store: dict[tuple[int, int], deque[int]] = defaultdict(lambda: deque(maxlen=_MAX_PER_USER))


def record(chat_id: int, user_id: int, message_id: int) -> None:
    _store[(chat_id, user_id)].append(message_id)


def pop_all(chat_id: int, user_id: int) -> list[int]:
    """Забирает и очищает все известные ID сообщений пользователя."""
    dq = _store.pop((chat_id, user_id), None)
    return list(dq) if dq else []
