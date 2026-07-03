"""Лимит на одинаковые (повторяющиеся) сообщения от одного пользователя.

Счётчик считается ПОСУТОЧНО и обнуляется в 00:00 по Пермскому времени (UTC+5):
за один календарный день (по Перми) разрешено не более DUPLICATE_LIMIT одинаковых
сообщений; в полночь счётчик сбрасывается у всех.

Важно: администраторов чата Telegram API удалять НЕ позволяет. Если повтор пришёл
от такого пользователя, бот не сможет удалить сообщение и уведомит админов бота с
рекомендацией снять с нарушителя права администратора.
"""
import re
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import (
    mention,
    mute_user,
    notify_admins,
    quick_action_markup,
    safe_delete,
)

# Сколько разных текстов хранить на пользователя (защита от роста памяти)
_TRACK_PER_USER = 40
# Мут за спам повторами — на сутки
_DUP_MUTE_SECONDS = 24 * 3600
# Пермское время (UTC+5) — по нему считаются календарные сутки
_PERM_TZ = timezone(timedelta(hours=5))

_whitespace = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _whitespace.sub(" ", text.strip().lower())


def _perm_day() -> int:
    """Номер календарного дня по Перми (меняется в 00:00 по Перми → сброс счётчика)."""
    return datetime.now(_PERM_TZ).toordinal()


class AntiDuplicateMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        # (chat_id, user_id) -> OrderedDict[text_hash, [день, счётчик]]
        self._seen: dict[tuple[int, int], "OrderedDict[int, list[int]]"] = defaultdict(
            OrderedDict
        )

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
        today = _perm_day()

        bucket = self._seen[key]
        entry = bucket.get(text_hash)
        if entry is None or entry[0] != today:
            # Новый текст или наступили новые сутки по Перми → счётчик с нуля
            entry = [today, 0]
            bucket[text_hash] = entry
        bucket.move_to_end(text_hash)
        entry[1] += 1

        # Ограничиваем число отслеживаемых текстов на пользователя
        while len(bucket) > _TRACK_PER_USER:
            bucket.popitem(last=False)

        count = entry[1]
        if count <= limit:
            return await handler(event, data)

        # Превышен лимит — удаляем повторную копию
        deleted = await safe_delete(bot, event.chat.id, event.message_id)
        label = f"@{event.from_user.username}" if event.from_user.username else event.from_user.full_name
        snippet = _whitespace.sub(" ", (event.text or event.caption or "")).strip()[:200] or "(медиа)"
        uid = event.from_user.id

        # Реагируем один раз — в момент превышения лимита (дальше юзер уже в муте)
        if count != limit + 1:
            return None

        markup = quick_action_markup(event.chat.id, uid)

        # Уведомление о лимите в чат — ВСЕГДА при срабатывании (и участнику, и админу)
        warn_text = (
            (await settings.get("dup_warn_text"))
            .replace("{user}", mention(event.from_user))
            .replace("{limit}", str(limit))
            .replace("{hours}", str(config.duplicate_window_hours))
        )
        if warn_text.strip():
            try:
                await bot.send_message(event.chat.id, warn_text)
            except Exception:
                pass

        # deleted=True → обычный участник (не админ чата): мьютим на сутки
        if deleted:
            until = int(time.time()) + _DUP_MUTE_SECONDS
            muted = await mute_user(bot, event.chat.id, uid, until_date=until)
            if muted:
                await db.add_action(event.chat.id, uid, "mute", label)
                await db.add_log(f"🔇 Мьют {label} · авто: повтор одного сообщения (сутки)")
            note = "Выдан мут на сутки." if muted else "Не удалось замьютить (нет прав?)."
            await notify_admins(
                bot,
                f"🧹 {label} (id {uid}) спамит повтором в «{event.chat.title}». {note}\n💬 {snippet}",
                reply_markup=markup,
            )
        else:
            # Сообщение не удалилось → это администратор чата, бот его трогать не может
            await notify_admins(
                bot,
                f"⚠️ {label} (id {uid}) спамит повторами в «{event.chat.title}», но бот НЕ может "
                f"удалить/замьютить — это администратор чата. Снимите с него права.\n💬 {snippet}",
                reply_markup=markup,
            )
        return None
