from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import settings_store as settings
from bot.config import config
from bot.filters.admin import IsChatOwner
from bot.utils.access import is_bot_admin
from bot.utils.moderation import (
    ban_user,
    mention,
    menu_markup,
    mute_user,
    notify_admins,
    punish_log,
    purge_user_messages,
    safe_delete,
)

router = Router(name="check")

PERM_TZ = timezone(timedelta(hours=5))  # Пермь = UTC+5
# «Полный» мьют по кнопке «Ограничить доступ» — на 1 год
RESTRICT_SECONDS = 365 * 24 * 3600


@router.message(Command("check"), IsChatOwner())
async def cmd_check(message: Message, bot: Bot) -> None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответь командой /check на сообщение пользователя, которого проверяем.")
        return

    target = message.reply_to_message.from_user
    if is_bot_admin(target.id):
        await message.reply("⛔ Это админ бота — проверка не требуется.")
        return

    offset_hours = await settings.get_int("check_offset_hours", 1)
    deadline = (datetime.now(PERM_TZ) + timedelta(hours=offset_hours)).strftime("%H:%M")
    template = await settings.get("check_template")
    text = template.replace("{time}", deadline)

    await message.reply_to_message.reply(text)
    await safe_delete(bot, message.chat.id, message.message_id)

    label = f"@{target.username}" if target.username else target.full_name
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать", callback_data=f"chk_ban:{message.chat.id}:{target.id}"
                ),
                InlineKeyboardButton(
                    text="🔒 Ограничить доступ", callback_data=f"chk_mute:{message.chat.id}:{target.id}"
                ),
            ],
            [InlineKeyboardButton(text="☰ Открыть панель", callback_data="openmenu")],
        ]
    )
    await notify_admins(
        bot,
        f"🔎 Запущена проверка пользователя {label} (id {target.id})\n"
        f"Чат: «{message.chat.title}»\nСрок до {deadline} (Пермь).\n\nВыбери действие:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("chk_ban:") | F.data.startswith("chk_mute:"))
async def on_check_action(callback: CallbackQuery, bot: Bot) -> None:
    if not is_bot_admin(callback.from_user.id):
        await callback.answer("Только для админов бота.", show_alert=True)
        return

    action, chat_id_str, user_id_str = callback.data.split(":")
    chat_id, user_id = int(chat_id_str), int(user_id_str)

    if action == "chk_ban":
        ok = await ban_user(bot, chat_id, user_id)
        if ok:
            # Публикуем в чате тот же текст, что и /ban 2, с упоминанием
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                text = (await settings.get("ban_preset_2")).replace("{user}", mention(member.user))
                await bot.send_message(chat_id, text)
            except Exception:
                pass
            result = f"🚫 Пользователь {user_id} заблокирован."
        else:
            result = "⚠️ Не удалось заблокировать (админ чата?)."
    else:
        until = int(datetime.now(tz=timezone.utc).timestamp()) + RESTRICT_SECONDS
        ok = await mute_user(bot, chat_id, user_id, until_date=until)
        if ok:
            # Ограничение не удаляет сообщения само — чистим известные вручную
            await purge_user_messages(bot, chat_id, user_id)
            # Публикуем в чате текст-предупреждение об ограничении с упоминанием
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                text = (await settings.get("restrict_message")).replace("{user}", mention(member.user))
                await bot.send_message(chat_id, text)
            except Exception:
                pass
            result = f"🔒 Пользователю {user_id} выданы все ограничения."
        else:
            result = "⚠️ Не удалось ограничить (админ чата?)."

    actor = f"@{callback.from_user.username}" if callback.from_user.username else str(callback.from_user.id)
    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ {result}\nВыполнил: {actor}",
        reply_markup=menu_markup(),
    )
    await callback.answer("Готово")
    if ok:
        if action == "chk_ban":
            hist = f"🚫 Бан id {user_id} · ручной {actor}: проверка"
            punish_action = "ban"
        else:
            hist = f"🔇 Мьют id {user_id} · ручной {actor}: проверка"
            punish_action = "mute"
        await punish_log(
            bot, config.log_chat_id, hist,
            action=punish_action, chat_id=chat_id, user_id=user_id, label=f"id {user_id}",
        )
