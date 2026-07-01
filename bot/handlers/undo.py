"""Отмена действий бота по кнопке (в ЛС-уведомлениях и в панели восстановления).

undo:ban:<chat>:<user>  — разбанить и дать ссылку для возврата в чат.
undo:mute:<chat>:<user> — снять все ограничения.
"""
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from bot import database as db
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import create_invite, unban_user, unmute_user

router = Router(name="undo")


@router.callback_query(F.data.startswith("undo:"))
async def on_undo(callback: CallbackQuery, bot: Bot) -> None:
    if not is_bot_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return

    _, action, chat_str, user_str = callback.data.split(":")
    chat_id, user_id = int(chat_str), int(user_str)

    if action == "ban":
        ok = await unban_user(bot, chat_id, user_id)
        if ok:
            link = await create_invite(bot, chat_id)
            result = f"↩️ Пользователь {user_id} разбанен."
            if link:
                result += f"\nСсылка для возврата (отправь ему): {link}"
        else:
            result = "⚠️ Не удалось снять бан."
    elif action == "mute":
        ok = await unmute_user(bot, chat_id, user_id)
        result = f"↩️ С пользователя {user_id} сняты все ограничения." if ok else "⚠️ Не удалось снять ограничения."
    else:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    actor = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    if ok:
        await db.delete_action(chat_id, user_id, action)
        # Пишем в историю и лог-чат, но НЕ рассылаем новое сообщение админам —
        # редактируем то, на котором нажали, чтобы не плодить дубли.
        try:
            await db.add_log(f"{result} · отменил {actor}")
        except Exception:
            pass
        if config.log_chat_id:
            try:
                await bot.send_message(config.log_chat_id, f"{result}\nОтменил: {actor}")
            except Exception:
                pass

    base = callback.message.text or ""
    suffix = f"\n\n{result}" + (f"\nОтменил: {actor}" if ok else "")
    try:
        # reply_markup не передаём — кнопки убираются
        await callback.message.edit_text(base + suffix)
    except Exception:
        pass
    await callback.answer("Готово" if ok else "Ошибка")
