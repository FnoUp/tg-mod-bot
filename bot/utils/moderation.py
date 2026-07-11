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
from aiogram.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup

from bot import database as db
from bot.config import config
from bot.utils import msgtrack
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


def mention(user) -> str:
    """Возвращает кликабельное упоминание пользователя для HTML-разметки."""
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = (getattr(user, "full_name", None) or str(user.id)).replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def _safe(coro: Awaitable) -> bool:
    try:
        await coro
        return True
    except Exception as exc:  # noqa: BLE001 — намеренно не роняем бота
        logger.warning("Модерационное действие не выполнено: %s", exc)
        return False


async def purge_user_messages(bot: Bot, chat_id: int, user_id: int) -> None:
    """Удаляет все известные боту сообщения пользователя (сверх revoke_messages)."""
    for message_id in msgtrack.pop_all(chat_id, user_id):
        await _safe(bot.delete_message(chat_id, message_id))


# Чаты, где уже предупредили об отсутствии права удалять сообщения (раз за сессию)
_warned_no_delete: set[int] = set()


async def can_delete_messages(bot: Bot, chat_id: int) -> bool:
    """Есть ли у бота право администратора «Удаление сообщений» в этом чате."""
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        return bool(getattr(member, "can_delete_messages", False))
    except Exception:
        return True  # не смогли проверить — не паникуем


async def _warn_if_cant_delete(bot: Bot, chat_id: int) -> None:
    if chat_id in _warned_no_delete:
        return
    if not await can_delete_messages(bot, chat_id):
        _warned_no_delete.add(chat_id)
        await notify_admins(
            bot,
            "⚠️ У меня НЕТ права администратора «Удаление сообщений» в этом чате — "
            "поэтому сообщения забаненных не стираются. Выдайте боту это право "
            "(Настройки чата → Администраторы → бот → «Удаление сообщений»).",
        )


async def ban_user(
    bot: Bot,
    chat_id: int,
    user_id: int,
    until_date: int | None = None,
    revoke_messages: bool = True,
) -> bool:
    if is_bot_admin(user_id):
        logger.info("Отказ: попытка забанить админа бота %s", user_id)
        return False
    # revoke_messages=True — Telegram удаляет ВСЕ сообщения пользователя в чате
    ok = await _safe(
        bot.ban_chat_member(
            chat_id, user_id, until_date=until_date, revoke_messages=revoke_messages
        )
    )
    if ok:
        await purge_user_messages(bot, chat_id, user_id)
        await _warn_if_cant_delete(bot, chat_id)
    return ok


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


def _menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="☰ Открыть панель", callback_data="openmenu")


def menu_markup() -> InlineKeyboardMarkup:
    """Клавиатура с единственной кнопкой возврата в панель — для любых сообщений в ЛС."""
    return InlineKeyboardMarkup(inline_keyboard=[[_menu_button()]])


def quick_action_markup(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Кнопки быстрых действий Мут(вечный)/Размут/Бан/Кик + панель (handlers/modactions)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔇 Мут (вечный)", callback_data=f"fm_mute:{chat_id}:{user_id}"),
            InlineKeyboardButton(text="🔊 Размут", callback_data=f"fm_unmute:{chat_id}:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🚫 Бан", callback_data=f"fm_ban:{chat_id}:{user_id}"),
            InlineKeyboardButton(text="👢 Кик", callback_data=f"fm_kick:{chat_id}:{user_id}"),
        ],
        [_menu_button()],
    ])


async def log_action(
    bot: Bot, log_chat_id: int | None, text: str, detail: str | None = None
) -> None:
    """Пишет событие в историю (БД) и в ЛС/лог-чат.

    text   — краткая строка, попадает и в историю, и в ЛС.
    detail — доп. содержимое (напр. текст нарушившего сообщения): показывается
             ТОЛЬКО в ЛС/лог-чате, в компактную историю не пишется.
    """
    try:
        await db.add_log(text)
    except Exception:
        pass
    dm_text = f"{text}\n💬 {detail}" if detail else text
    markup = InlineKeyboardMarkup(inline_keyboard=[[_menu_button()]])
    await notify_admins(bot, dm_text, reply_markup=markup)
    if log_chat_id:
        await _safe(bot.send_message(log_chat_id, dm_text))


async def create_invite(bot: Bot, chat_id: int) -> str | None:
    """Одноразовая ссылка-приглашение для возврата пользователя (бот не может
    добавить его в чат сам — Telegram это запрещает)."""
    try:
        link = await bot.create_chat_invite_link(chat_id, member_limit=1, name="Возврат")
        return link.invite_link
    except Exception:
        return None


async def punish_log(
    bot: Bot,
    log_chat_id: int | None,
    text: str,
    *,
    action: str,
    chat_id: int,
    user_id: int,
    label: str,
    detail: str | None = None,
) -> None:
    """Как log_action, но пишет структурированное действие (для статистики и
    восстановления) и прикладывает к ЛС-уведомлению кнопку «Отменить».

    detail показывается только в ЛС/лог-чате, в историю не пишется.
    """
    try:
        await db.add_log(text)
    except Exception:
        pass
    try:
        await db.add_action(chat_id, user_id, action, label)
    except Exception:
        pass
    dm_text = f"{text}\n💬 {detail}" if detail else text
    rows = []
    if action in ("ban", "mute"):
        undo_label = "↩️ Разбанить и вернуть" if action == "ban" else "↩️ Снять ограничения"
        rows.append([InlineKeyboardButton(
            text=undo_label, callback_data=f"undo:{action}:{chat_id}:{user_id}")])
    rows.append([_menu_button()])
    await notify_admins(bot, dm_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    if log_chat_id:
        await _safe(bot.send_message(log_chat_id, dm_text))
