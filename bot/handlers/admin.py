import time

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.filters.admin import IsChatOwner
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
# Команды в чате: только владелец чата, ADMIN_IDS или отправка «от сообщества»
router.message.filter(IsChatOwner())


def _target(message: Message) -> tuple[int, str] | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, f"@{user.username}" if user.username else user.full_name
    return None


def _reason(message: Message, command_len: int = 1) -> str:
    parts = (message.text or "").split(maxsplit=command_len)
    return parts[command_len] if len(parts) > command_len else "не указана"


def _snippet(message: Message) -> str:
    """Текст сообщения, на которое отвечают командой (для истории)."""
    reply = message.reply_to_message
    if not reply:
        return ""
    text = reply.text or reply.caption or ""
    return " ".join(text.split())[:150]


def _with_msg(text: str, snippet: str) -> str:
    return f"{text}\n💬 {snippet}" if snippet else text


def _actor(message: Message) -> str:
    user = message.from_user
    if user and user.username:
        return f"@{user.username}"
    if user and not user.is_bot:
        return str(user.id)
    if message.sender_chat:
        return "сообщество"
    return "админ"


async def _cleanup(bot: Bot, message: Message) -> None:
    """Удаляет само командное сообщение, чтобы не засорять чат (best-effort;
    сообщение владельца/создателя Telegram удалить не даст — тогда останется)."""
    await safe_delete(bot, message.chat.id, message.message_id)


async def _guarded_target(message: Message) -> tuple[int, str] | None:
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
    snippet = _snippet(message)
    actor = _actor(message)

    # /ban 1 — тихий бан без текста
    if arg == "1":
        ok = await ban_user(bot, message.chat.id, user_id)
        await db.reset_warns(message.chat.id, user_id)
        if ok:
            await safe_delete(bot, message.chat.id, message.reply_to_message.message_id)
            await punish_log(
                bot, config.log_chat_id,
                _with_msg(f"🚫 Бан {label} · ручной {actor}: тихий", snippet),
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")
        await _cleanup(bot, message)
        return

    # /ban 2 — бан + публикация текста-пресета с упоминанием
    if arg == "2":
        preset_text = (await settings.get("ban_preset_2")).replace("{user}", mention(ru))
        ok = await ban_user(bot, message.chat.id, user_id)
        await db.reset_warns(message.chat.id, user_id)
        if ok:
            await message.reply_to_message.reply(preset_text)
            await punish_log(
                bot, config.log_chat_id,
                _with_msg(f"🚫 Бан {label} · ручной {actor}: пресет 2", snippet),
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")
        await _cleanup(bot, message)
        return

    # Обычный /ban [причина] — тихо, уведомление только в ЛС
    reason = arg if arg != "не указана" else "без причины"
    ok = await ban_user(bot, message.chat.id, user_id)
    await db.reset_warns(message.chat.id, user_id)
    if ok:
        await punish_log(
            bot, config.log_chat_id,
            _with_msg(f"🚫 Бан {label} · ручной {actor}: {reason}", snippet),
            action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось забанить (возможно, цель — админ чата).")
    await _cleanup(bot, message)


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.reply("Использование: /unban <user_id>")
        return
    user_id = int(parts[1])
    if await unban_user(bot, message.chat.id, user_id):
        await log_action(bot, config.log_chat_id, f"↩️ Разбан id {user_id} · {_actor(message)}")
    else:
        await message.reply("⚠️ Не удалось разбанить.")
    await _cleanup(bot, message)


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    # /kick — тихий: в беседе ничего не пишем
    if await kick_user(bot, message.chat.id, user_id):
        await punish_log(
            bot, config.log_chat_id,
            _with_msg(f"👢 Кик {label} · ручной {_actor(message)}", _snippet(message)),
            action="kick", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось кикнуть (возможно, цель — админ чата).")
    await _cleanup(bot, message)


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    parts = (message.text or "").split(maxsplit=2)
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else config.flood_mute_minutes
    until = int(time.time()) + minutes * 60
    if await mute_user(bot, message.chat.id, user_id, until_date=until):
        await punish_log(
            bot, config.log_chat_id,
            _with_msg(f"🔇 Мьют {label} · ручной {_actor(message)}: {minutes} мин", _snippet(message)),
            action="mute", chat_id=message.chat.id, user_id=user_id, label=label,
        )
    else:
        await message.reply("⚠️ Не удалось замьютить (возможно, цель — админ чата).")
    await _cleanup(bot, message)


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return
    user_id, label = target
    if await unmute_user(bot, message.chat.id, user_id):
        await log_action(bot, config.log_chat_id, f"🔊 Снятие мьюта {label} · {_actor(message)}")
    else:
        await message.reply("⚠️ Не удалось размьютить.")
    await _cleanup(bot, message)


@router.message(Command("warn"))
async def cmd_warn(message: Message, bot: Bot) -> None:
    target = await _guarded_target(message)
    if not target:
        return
    user_id, label = target
    reason = _reason(message)
    warn_limit = await settings.get_int("warn_limit", config.warn_limit)
    count = await db.add_warn(message.chat.id, user_id)
    if count >= warn_limit:
        if await ban_user(bot, message.chat.id, user_id):
            await db.reset_warns(message.chat.id, user_id)
            await punish_log(
                bot, config.log_chat_id, f"🚫 Бан {label} · лимит предупреждений · {_actor(message)}",
                action="ban", chat_id=message.chat.id, user_id=user_id, label=label,
            )
        else:
            await message.reply("⚠️ Лимит предупреждений превышен, но забанить не удалось.")
    else:
        display_reason = reason if reason != "не указана" else "без причины"
        await log_action(
            bot, config.log_chat_id,
            _with_msg(
                f"⚠️ Предупреждение {label} · ручной {_actor(message)}: "
                f"{display_reason} ({count}/{warn_limit})",
                _snippet(message),
            ),
        )
    await _cleanup(bot, message)


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, bot: Bot) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return
    user_id, label = target
    await db.reset_warns(message.chat.id, user_id)
    await log_action(bot, config.log_chat_id, f"♻️ Сброшены предупреждения {label} · {_actor(message)}")
    await _cleanup(bot, message)


@router.message(Command("warns"))
async def cmd_warns(message: Message, bot: Bot) -> None:
    target = _target(message)
    if not target:
        await message.reply("Ответь этой командой на сообщение пользователя.")
        return
    user_id, label = target
    warn_limit = await settings.get_int("warn_limit", config.warn_limit)
    count = await db.get_warns(message.chat.id, user_id)
    await message.answer(f"{label}: {count}/{warn_limit} предупреждений")
    await _cleanup(bot, message)


@router.message(Command("id"))
async def cmd_id(message: Message, bot: Bot) -> None:
    if message.reply_to_message and message.reply_to_message.from_user:
        await message.answer(f"ID: {message.reply_to_message.from_user.id}")
    elif message.from_user:
        await message.answer(f"ID: {message.from_user.id}")
    await _cleanup(bot, message)


@router.message(Command("modhelp"))
async def cmd_help(message: Message, bot: Bot) -> None:
    await message.answer(
        "🛠 <b>Команды модерации</b> (в ответ на сообщение пользователя):\n"
        "/ban [причина] — забанить (тихо)\n"
        "/ban 1 — тихий бан без текста\n"
        "/ban 2 — бан + текст-пресет с упоминанием\n"
        "/unban &lt;user_id&gt; — разбанить\n"
        "/kick — кикнуть (тихо)\n"
        "/mute &lt;минуты&gt; — замьютить (тихо)\n"
        "/unmute — размьютить\n"
        "/warn [причина] — предупреждение\n"
        "/unwarn — сбросить предупреждения\n"
        "/warns — сколько предупреждений\n"
        "/check — проверка с кнопками в ЛС\n"
        "/id — узнать Telegram ID\n\n"
        "Команды работают только у владельца чата и админов бота. "
        "Своё командное сообщение бот старается удалять."
    )
    await _cleanup(bot, message)
