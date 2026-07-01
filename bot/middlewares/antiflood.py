import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import log_action, mute_user, safe_delete


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

        key = (event.chat.id, event.from_user.id)
        now = time.monotonic()
        history = self._history[key]
        history.append(now)
        cutoff = now - config.flood_interval_seconds
        while history and history[0] < cutoff:
            history.popleft()

        if len(history) <= config.flood_message_limit:
            return await handler(event, data)

        history.clear()
        bot: Bot = data["bot"]
        until = int(time.time()) + config.flood_mute_minutes * 60
        muted = await mute_user(bot, event.chat.id, event.from_user.id, until_date=until)
        await safe_delete(bot, event.chat.id, event.message_id)
        if muted:
            label = f"@{event.from_user.username}" if event.from_user.username else event.from_user.full_name
            await log_action(
                bot,
                config.log_chat_id,
                f"🔇 Флуд: {label} замьючен на {config.flood_mute_minutes} мин. "
                f"в чате «{event.chat.title}»",
            )
        return None
