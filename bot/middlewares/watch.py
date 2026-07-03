"""Наблюдатель сообщений: запоминает ID сообщений (для зачистки при бане) и
уведомляет админов о ПЕРВОМ сообщении нового пользователя.

Уведомление о первом сообщении можно выключить в панели (тумблер в «Модули»).
"""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import Message

from bot import database as db
from bot import settings_store as settings
from bot.utils import msgtrack
from bot.utils.access import is_bot_admin
from bot.utils.moderation import mention, menu_markup, notify_admins


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Является ли пользователь администратором или создателем чата."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


class MessageWatchMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if (
            event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
            and event.from_user is not None
            and not event.from_user.is_bot
        ):
            uid = event.from_user.id
            msgtrack.record(event.chat.id, uid, event.message_id)

            if not is_bot_admin(uid) and await settings.get_bool("notify_first_msg_enabled"):
                # take_pending_first вернёт True только для реально вступивших
                # участников и только один раз (на их первом сообщении)
                if await db.take_pending_first(event.chat.id, uid):
                    bot: Bot = data["bot"]
                    if not await _is_chat_admin(bot, event.chat.id, uid):
                        text = event.text or event.caption or ""
                        snippet = " ".join(text.split())[:200] or "(медиа / без текста)"
                        await notify_admins(
                            bot,
                            f"🆕 Первое сообщение от {mention(event.from_user)} "
                            f"в «{event.chat.title}»:\n💬 {snippet}",
                            reply_markup=menu_markup(),
                        )

        return await handler(event, data)
