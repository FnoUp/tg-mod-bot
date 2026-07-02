"""Единая точка контроля доступа.

Власть над ботом и иммунитет от модерации есть у двух групп:
  • базовые админы из ADMIN_IDS (.env) — задаются при установке, убрать нельзя;
  • добавленные из панели — хранятся в БД, назначаются владельцем через меню.

Статус «администратор чата» НЕ даёт никаких привилегий: в чате могут быть
админы-рекламщики без прав, и они подчиняются модерации наравне со всеми.
"""
from bot.config import config

_env_admins: frozenset[int] = frozenset(config.admin_ids)
_extra_admins: set[int] = set()


def is_bot_admin(user_id: int | None) -> bool:
    """True для базовых (.env) и добавленных из панели админов."""
    return user_id is not None and (user_id in _env_admins or user_id in _extra_admins)


def set_extra_admins(user_ids) -> None:
    """Полностью заменяет список добавленных админов (при старте — из БД)."""
    _extra_admins.clear()
    _extra_admins.update(int(u) for u in user_ids)


def add_extra_admin(user_id: int) -> None:
    _extra_admins.add(int(user_id))


def remove_extra_admin(user_id: int) -> None:
    _extra_admins.discard(int(user_id))


def get_env_admins() -> list[int]:
    return sorted(_env_admins)


def get_extra_admins() -> list[int]:
    return sorted(_extra_admins)
