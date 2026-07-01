import time

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.filters.admin import IsBotAdmin
from bot.utils.access import is_bot_admin
from bot.utils.moderation import (
    ban_user,
    kick_user,
    log_action,
    mention,
    mute_user,
    punish_log,
    safe_delete,
    unban_user,
    unmute_user,
)

router = Router(name="admin")
# Все команды модерации доступны ТОЛЬКО доверенным админам из ADMIN_IDS
router.message.filter(IsBotAdmin())


def _target(message: Message) -> tuple[int, str] | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, f"@{user.username}" if user.username else user.full_name
    return None


def _reason(message: Message, command_len: int = 1) -> str:
    parts = (message.text or "").split(maxsplit=command_len)
    return parts[command_len] if len(parts) > command_len else "не указана"


def _actor(message: Message) -> str:
    return f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)


async def _guarded_target(message: Message) -> tuple[int, str] | None:
    """Возвращает цель или None, отвечая пользователю о причине отказа."""
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return None
    if is_bot_admin(target[0]):
        await message.reply("⛔ Это админ бота — действие к нему не применяется.")
        return None
    return target


@router.message(Command("ban"))
async def cmd_ban(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    ru = message.reply_to_message.from_user
    arg = _reason(message)

    # /ban 1 — тихий бан без текста (сообщение нарушителя и команда удаляются)
    if arg == "1":
        ok = await ban_user(bot, message.chat.id, user_id)
        await db.reset_warns(message.chat.id, user_id)
        if ok:
            await safe_delete(bot, message.chat.id, message.reply_to_message.message_id)
            await safe_delete(bot, message.chat.id, message.message_id)
            await punish_log(
                bot, config.log_chat_id, f"🚫 {label} тихо забанен (/ban 1) админом {_actor(message)}",
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")
        return

    # /ban 2 — бан + публикация текста-пресета с упоминанием нарушителя
    if arg == "2":
        preset_text = (await settings.get("ban_preset_2")).replace("{user}", mention(ru))
        ok = await ban_user(bot, message.chat.id, user_id)
        await db.reset_warns(message.chat.id, user_id)
        if ok:
            await message.reply_to_message.reply(preset_text)
            await safe_delete(bot, message.chat.id, message.message_id)
            await punish_log(
                bot, config.log_chat_id, f"🚫 {label} забанен (/ban 2) админом {_actor(message)}",
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")
        return

    ok = await ban_user(bot, message.chat.id, user_id)
    await db.reset_warns(message.chat.id, user_id)
    if ok:
        await message.reply(f"🚫 {label} забанен. Причина: {arg}")
        await punish_log(
            bot, config.log_chat_id, f"🚫 {label} забанен админом {_actor(message)}. Причина: {arg}",
            action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.reply("Использование: /unban <user_id>")
        return
    user_id = int(parts[1])
    if await unban_user(bot, message.chat.id, user_id):
        await message.reply(f"✅ Пользователь {user_id} разбанен.")
        await log_action(
            bot, config.log_chat_id, f"✅ Пользователь {user_id} разбанен админом {_actor(message)}"
        )
    else:
        await message.reply("⚠️ Не удалось разбанить.")


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    if await kick_user(bot, message.chat.id, user_id):
        await message.reply(f"👢 {label} исключён из чата.")
        await punish_log(
            bot, config.log_chat_id, f"👢 {label} кикнут админом {_actor(message)}",
            action="kick", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось кикнуть (возможно, цель — админ чата).")


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    parts = (message.text or "").split(maxsplit=2)
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else config.flood_mute_minutes
    reason = parts[2] if len(parts) > 2 else "не указана"
    until = int(time.time()) + minutes * 60
    if await mute_user(bot, message.chat.id, user_id, until_date=until):
        await message.reply(f"🔇 {label} замьючен на {minutes} мин. Причина: {reason}")
        await punish_log(
            bot, config.log_chat_id, f"🔇 {label} замьючен на {minutes} мин. админом {_actor(message)}",
            action="mute", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось замьютить (возможно, цель — админ чата).")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя, которого нужно размьютить.")
        return
    user_id, label = target
    if await unmute_user(bot, message.chat.id, user_id):
        await message.reply(f"🔊 {label} размьючен.")
    else:
        await message.reply("⚠️ Не удалось размьютить.")


@router.message(Command("warn"))
async def cmd_warn(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    reason = _reason(message)
    count = await db.add_warn(message.chat.id, user_id)
    await message.reply(f"⚠️ {label} получил предупреждение ({count}/{config.warn_limit}). Причина: {reason}")
    if count >= config.warn_limit:
        if await ban_user(bot, message.chat.id, user_id):
            await db.reset_warns(message.chat.id, user_id)
            await message.reply(f"🚫 {label} забанен: превышен лимит предупреждений.")
            await punish_log(
                bot, config.log_chat_id, f"🚫 {label} забанен: лимит предупреждений",
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Лимит предупреждений превышен, но забанить не удалось (админ чата?).")


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return
    user_id, label = target
    await db.reset_warns(message.chat.id, user_id)
    await message.reply(f"✅ Предупреждения {label} сброшены.")


@router.message(Command("warns"))
async def cmd_warns(message: Message) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return
    user_id, label = target
    count = await db.get_warns(message.chat.id, user_id)
    await message.reply(f"{label}: {count}/{config.warn_limit} предупреждений")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        await message.reply(f"ID: {user.id}")
    else:
        await message.reply(f"ID: {message.from_user.id}")


@router.message(Command("modhelp"))
async def cmd_help(message: Message) -> None:
    await message.reply(
        "Команды модерации (в ответ на сообщение пользователя):\n"
        "/ban [причина] — забанить\n"
        "/ban 1 — тихий бан без текста\n"
        "/ban 2 — бан + текст с упоминанием (из панели)\n"
        "/unban <user_id> — разбанить\n"
        "/kick [причина] — кикнуть\n"
        "/mute <минуты> [причина] — замьютить\n"
        "/unmute — размьютить\n"
        "/warn [причина] — предупреждение\n"
        "/unwarn — сбросить предупреждения\n"
        "/warns — показать число предупреждений\n"
        "/check — выдать проверку с кнопками в ЛС\n"
        "/id — узнать telegram ID"
    )
