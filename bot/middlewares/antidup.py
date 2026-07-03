"""Лимит на одинаковые (повторяющиеся) сообщения от одного пользователя.

Сценарий: у некоторых участников есть админка в чате, и они публикуют одну и ту
же рекламу помногу раз в день. Модуль считает повторы одинакового текста и после
DUPLICATE_LIMIT удаляет последующие копии.

Важно: администраторов чата Telegram API удалять НЕ позволяет. Если повтор пришёл
от такого пользователя, бот не сможет удалить сообщение и уведомит админов бота с
рекомендацией снять с нарушителя права администратора.
"""
import re
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from bot import settings_store as settings
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import mention, notify_admins, quick_action_markup, safe_delete

# Сколько разных текстов хранить на пользователя (защита от роста памяти)
_TRACK_PER_USER = 40
# Интервал предупреждения нарушителя в чате — раз в 24 часа на человека
_WARN_INTERVAL = 24 * 3600

_whitespace = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _whitespace.sub(" ", text.strip().lower())


class AntiDuplicateMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        # (chat_id, user_id) -> OrderedDict[text_hash, deque[timestamps]]
        self._seen: dict[tuple[int, int], "OrderedDict[int, deque[float]]"] = defaultdict(
            OrderedDict
        )
        # (chat_id, user_id) -> время последнего предупреждения в чате (wall clock)
        self._last_warn: dict[tuple[int, int], float] = {}

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
        if not await settings.get_bool("antidup_enabled"):
            return await handler(event, data)

        normalized = _normalize(event.text or event.caption or "")
        if len(normalized) < config.duplicate_min_length:
            return await handler(event, data)

        bot: Bot = data["bot"]
        limit = await settings.get_int("duplicate_limit", config.duplicate_limit)
        key = (event.chat.id, event.from_user.id)
        text_hash = hash(normalized)
        now = time.monotonic()
        window = config.duplicate_window_hours * 3600

        bucket = self._seen[key]
        timestamps = bucket.get(text_hash)
        if timestamps is None:
            timestamps = deque()
            bucket[text_hash] = timestamps
        bucket.move_to_end(text_hash)

        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()
        timestamps.append(now)

        # Ограничиваем число отслеживаемых текстов на пользователя
        while len(bucket) > _TRACK_PER_USER:
            bucket.popitem(last=False)

        count = len(timestamps)
        if count <= limit:
            return await handler(event, data)

        # Превышен лимит — удаляем повторную копию
        deleted = await safe_delete(bot, event.chat.id, event.message_id)
        label = f"@{event.from_user.username}" if event.from_user.username else event.from_user.full_name
        snippet = _whitespace.sub(" ", (event.text or event.caption or "")).strip()[:200] or "(медиа)"
        uid = event.from_user.id

        # Одноразовое (раз в 24 ч) предупреждение самому нарушителю в чат
        if deleted:
            wall = time.time()
            if wall - self._last_warn.get(key, 0) >= _WARN_INTERVAL:
                self._last_warn[key] = wall
                warn_text = (await settings.get("dup_warn_text")).replace(
                    "{user}", mention(event.from_user)
                )
                if warn_text.strip():
                    try:
                        await bot.send_message(event.chat.id, warn_text)
                    except Exception:
                        pass

        # Уведомляем админов только в момент первого превышения, чтобы не спамить ЛС
        if count == limit + 1:
            markup = quick_action_markup(event.chat.id, uid)
            if deleted:
                await notify_admins(
                    bot,
                    f"🧹 {label} (id {uid}) повторяет одно и то же сообщение "
                    f"(лимит {limit} за {config.duplicate_window_hours} ч превышен) в «{event.chat.title}».\n"
                    f"💬 {snippet}",
                    reply_markup=markup,
                )
            else:
                await notify_admins(
                    bot,
                    f"⚠️ {label} (id {uid}) спамит повторами в «{event.chat.title}», но бот НЕ может "
                    f"удалить его сообщения — это администратор чата. Снимите с него права.\n"
                    f"💬 {snippet}",
                    reply_markup=markup,
                )
        return None
