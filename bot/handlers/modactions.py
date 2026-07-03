"""Быстрые кнопки модерации из ЛС-уведомлений (напр. про первое сообщение):
🔇 Мут / 🚫 Бан / 👢 Кик. Действуют на конкретного пользователя в конкретном чате.

Формат callback: fm_<action>:<chat_id>:<user_id>, где action ∈ mute|ban|kick.
"""
import time

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import database as db
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import ban_user, kick_user, mute_user, purge_user_messages

router = Router(name="modactions")

MUTE_SECONDS = 365 * 24 * 3600  # «замутить» — надолго, снимается кнопкой отмены


@router.callback_query(F.data.startswith("fm_"))
async def on_quick_action(callback: CallbackQuery, bot: Bot) -> None:
    if not is_bot_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return

    head, chat_str, user_str = callback.data.split(":")
    kind = head[3:]  # ban | mute | kick
    chat_id, user_id = int(chat_str), int(user_str)
    actor = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)

    rows: list[list[InlineKeyboardButton]] = []
    if kind == "ban":
        ok = await ban_user(bot, chat_id, user_id)
        if ok:
            await db.add_action(chat_id, user_id, "ban", f"id {user_id}")
            await db.add_log(f"🚫 Бан id {user_id} · ручной {actor}: из уведомления")
            rows.append([InlineKeyboardButton(
                text="↩️ Разбанить и вернуть", callback_data=f"undo:ban:{chat_id}:{user_id}")])
        result = "🚫 Забанен." if ok else "⚠️ Не удалось (админ чата?)."
    elif kind == "mute":
        until = int(time.time()) + MUTE_SECONDS
        ok = await mute_user(bot, chat_id, user_id, until_date=until)
        if ok:
            await purge_user_messages(bot, chat_id, user_id)
            await db.add_action(chat_id, user_id, "mute", f"id {user_id}")
            await db.add_log(f"🔇 Мьют id {user_id} · ручной {actor}: из уведомления")
            rows.append([InlineKeyboardButton(
                text="↩️ Снять ограничения", callback_data=f"undo:mute:{chat_id}:{user_id}")])
        result = "🔇 Замьючен." if ok else "⚠️ Не удалось (админ чата?)."
    elif kind == "kick":
        ok = await kick_user(bot, chat_id, user_id)
        if ok:
            await db.add_action(chat_id, user_id, "kick", f"id {user_id}")
            await db.add_log(f"👢 Кик id {user_id} · ручной {actor}: из уведомления")
        result = "👢 Кикнут." if ok else "⚠️ Не удалось (админ чата?)."
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    rows.append([InlineKeyboardButton(text="☰ Открыть панель", callback_data="openmenu")])
    base = callback.message.text or ""
    try:
        await callback.message.edit_text(
            f"{base}\n\n✅ {result}\nВыполнил: {actor}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        pass
    await callback.answer("Готово" if ok else "Ошибка")
