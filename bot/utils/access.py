"""Единая точка контроля доступа.

Власть над ботом и иммунитет от любой модерации есть ТОЛЬКО у пользователей,
чьи Telegram ID перечислены в ADMIN_IDS. Статус «администратор чата» намеренно
НЕ даёт никаких привилегий: в чате могут быть админы-рекламщики без прав,
и они должны подчиняться модерации наравне со всеми.
"""
from bot.config import config


def is_bot_admin(user_id: int | None) -> bool:
    """True только для доверенных ID из ADMIN_IDS."""
    return user_id is not None and user_id in config.admin_ids
