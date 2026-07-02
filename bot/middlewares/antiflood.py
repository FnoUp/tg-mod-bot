import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from bot import settings_store as settings
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import mute_user, punish_log, safe_delete


class AntiFloodMiddleware(BaseMiddleware):
    """Мьютит пользователя, если он шлёт сообщения слишком часто."""

    def __init__(self) -> None:
        self._history: dict[tuple[int, int], deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return await handler(event, data)
        if event.from_user is None or event.from_user.is_bot:
            return await handler(event, data)
        if is_bot_admin(event.from_user.id):
            return await handler(event, data)
        if not await settings.get_bool("antiflood_enabled"):
            return await handler(event, data)

        msg_limit = await settings.get_int("flood_message_limit", config.flood_message_limit)
        interval = await settings.get_int("flood_interval_seconds", config.flood_interval_seconds)
        mute_minutes = await settings.get_int("flood_mute_minutes", config.flood_mute_minutes)

        key = (event.chat.id, event.from_user.id)
        now = time.monotonic()
        history = self._history[key]
        history.append(now)
        cutoff = now - interval
        while history and history[0] < cutoff:
            history.popleft()

        if len(history) <= msg_limit:
            return await handler(event, data)

        history.clear()
        bot: Bot = data["bot"]
        until = int(time.time()) + mute_minutes * 60
        muted = await mute_user(bot, event.chat.id, event.from_user.id, until_date=until)
        await safe_delete(bot, event.chat.id, event.message_id)
        if muted:
            label = f"@{event.from_user.username}" if event.from_user.username else event.from_user.full_name
            await punish_log(
                bot,
                config.log_chat_id,
                f"🔇 Мьют {label} · авто: флуд, {mute_minutes} мин",
                action="mute", chat_id=event.chat.id, user_id=event.from_user.id, label=label,
            )
        return None
