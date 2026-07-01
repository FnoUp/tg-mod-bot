"""Безопасные обёртки над Telegram API для модерации.

Все действия — best-effort: любая ошибка (нет прав, цель уже вышла, цель —
администратор чата, которого бот трогать не может) перехватывается и не роняет
бота. Функции возвращают bool: True — действие выполнено, False — не удалось.

Дополнительная защита: ни ban/mute/kick НИКОГДА не применяются к ID из
ADMIN_IDS, даже если такой вызов случайно произойдёт.
"""
import logging
from typing import Awaitable

from aiogram import Bot
from aiogram.types import ChatPermissions, InlineKeyboardMarkup

from bot.config import config
from bot.utils.access import is_bot_admin

logger = logging.getLogger(__name__)

MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


async def _safe(coro: Awaitable) -> bool:
    try:
        await coro
        return True
    except Exception as exc:  # noqa: BLE001 — намеренно не роняем бота
        logger.warning("Модерационное действие не выполнено: %s", exc)
        return False


async def ban_user(bot: Bot, chat_id: int, user_id: int, until_date: int | None = None) -> bool:
    if is_bot_admin(user_id):
        logger.info("Отказ: попытка забанить админа бота %s", user_id)
        return False
    return await _safe(bot.ban_chat_member(chat_id, user_id, until_date=until_date))


async def mute_user(bot: Bot, chat_id: int, user_id: int, until_date: int | None = None) -> bool:
    if is_bot_admin(user_id):
        logger.info("Отказ: попытка замьютить админа бота %s", user_id)
        return False
    return await _safe(
        bot.restrict_chat_member(
            chat_id, user_id, permissions=MUTE_PERMISSIONS, until_date=until_date
        )
    )


async def kick_user(bot: Bot, chat_id: int, user_id: int) -> bool:
    if is_bot_admin(user_id):
        logger.info("Отказ: попытка кикнуть админа бота %s", user_id)
        return False
    ok = await _safe(bot.ban_chat_member(chat_id, user_id))
    if ok:
        await _safe(bot.unban_chat_member(chat_id, user_id, only_if_banned=True))
    return ok


async def unmute_user(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        chat = await bot.get_chat(chat_id)
        permissions = chat.permissions or FULL_PERMISSIONS
    except Exception:  # noqa: BLE001
        permissions = FULL_PERMISSIONS
    return await _safe(bot.restrict_chat_member(chat_id, user_id, permissions=permissions))


async def unban_user(bot: Bot, chat_id: int, user_id: int) -> bool:
    return await _safe(bot.unban_chat_member(chat_id, user_id, only_if_banned=True))


async def safe_delete(bot: Bot, chat_id: int, message_id: int) -> bool:
    return await _safe(bot.delete_message(chat_id, message_id))


async def notify_admins(
    bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Шлёт уведомление в ЛС каждому админу из ADMIN_IDS (best-effort)."""
    for admin_id in config.admin_ids:
        await _safe(bot.send_message(admin_id, text, reply_markup=reply_markup))


async def log_action(bot: Bot, log_chat_id: int | None, text: str) -> None:
    """Дублирует событие в ЛС всем админам и в лог-чат (если задан)."""
    await notify_admins(bot, text)
    if log_chat_id:
        await _safe(bot.send_message(log_chat_id, text))
