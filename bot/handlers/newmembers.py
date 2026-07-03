import time
from collections import defaultdict, deque

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import kick_user, log_action, mention, mute_user, punish_log, safe_delete

router = Router(name="newmembers")

RESTRICT_PERMISSIONS = ChatPermissions(can_send_messages=False)

# Анти-рейд: время входов по чатам и активные локдауны (в памяти)
_join_history: dict[int, deque[float]] = defaultdict(deque)
_lockdown_until: dict[int, float] = {}


async def _is_raid(chat_id: int) -> bool:
    """True, если сейчас идёт массовый вход (рейд) в этом чате."""
    now = time.monotonic()
    history = _join_history[chat_id]
    history.append(now)
    cutoff = now - config.raid_window_seconds
    while history and history[0] < cutoff:
        history.popleft()
    if len(history) >= config.raid_join_limit:
        _lockdown_until[chat_id] = now + config.raid_lockdown_minutes * 60
        return True
    return _lockdown_until.get(chat_id, 0) > now


@router.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot) -> None:
    antiraid_on = await settings.get_bool("antiraid_enabled")

    for user in message.new_chat_members:
        if user.is_bot or is_bot_admin(user.id):
            continue

        # Отмечаем вступившего — чтобы уведомить о его ПЕРВОМ сообщении
        await db.add_pending_first(message.chat.id, user.id)

        # Анти-рейд: при массовом входе новичков сразу мьютим (без капчи) и алертим админов
        if antiraid_on and await _is_raid(message.chat.id):
            until = int(time.time()) + config.raid_lockdown_minutes * 60
            label = f"@{user.username}" if user.username else user.full_name
            if await mute_user(bot, message.chat.id, user.id, until_date=until):
                await punish_log(
                    bot,
                    config.log_chat_id,
                    f"🔇 Мьют {label} · авто: антирейд (массовый вход)",
                    action="mute", chat_id=message.chat.id, user_id=user.id, label=label,
                )
            continue

        try:
            await bot.restrict_chat_member(
                message.chat.id, user.id, permissions=RESTRICT_PERMISSIONS
            )
        except Exception:
            continue
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Я не бот", callback_data=f"captcha_ok:{user.id}")]
            ]
        )
        sent = await message.answer(
            f"👋 {user.full_name}, нажми кнопку ниже в течение "
            f"{max(config.captcha_timeout_seconds // 60, 1)} мин., иначе будешь исключён.",
            reply_markup=keyboard,
        )
        await db.add_captcha(message.chat.id, user.id, sent.message_id)


@router.callback_query(F.data.startswith("captcha_ok:"))
async def on_captcha_ok(callback: CallbackQuery, bot: Bot) -> None:
    expected_user_id = int(callback.data.split(":", 1)[1])
    if callback.from_user.id != expected_user_id:
        await callback.answer("Эта кнопка не для тебя.", show_alert=True)
        return

    await db.pop_captcha(callback.message.chat.id, expected_user_id)
    try:
        chat = await bot.get_chat(callback.message.chat.id)
        permissions = chat.permissions or ChatPermissions(can_send_messages=True)
        await bot.restrict_chat_member(
            callback.message.chat.id, expected_user_id, permissions=permissions
        )
    except Exception:
        pass
    await safe_delete(bot, callback.message.chat.id, callback.message.message_id)
    await callback.answer("Добро пожаловать!")
    passer = f"@{callback.from_user.username}" if callback.from_user.username else str(expected_user_id)
    await log_action(bot, config.log_chat_id, f"✅ Капча пройдена · {passer}")

    # Приветствие + правила (если включено в панели)
    if await settings.get_bool("welcome_enabled"):
        welcome = (await settings.get("welcome_text")).replace("{user}", mention(callback.from_user))
        if welcome.strip():
            try:
                await bot.send_message(callback.message.chat.id, welcome)
            except Exception:
                pass


async def check_expired_captchas(bot: Bot) -> None:
    cutoff = int(time.time()) - config.captcha_timeout_seconds
    for chat_id, user_id, message_id in await db.get_expired_captchas(cutoff):
        await db.pop_captcha(chat_id, user_id)
        await kick_user(bot, chat_id, user_id)
        await safe_delete(bot, chat_id, message_id)
        await log_action(
            bot, config.log_chat_id, f"👢 Кик id {user_id} · не прошёл капчу вовремя · авто"
        )
