from typing import Any, Awaitable, Callable

import aiohttp
from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.types import Message

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.utils.access import is_bot_admin
from bot.utils.moderation import ban_user, log_action, notify_admins, punish_log, safe_delete
from bot.utils.text import contains_banned_word, contains_link, extract_links, is_whitelisted


async def _is_cas_banned(user_id: int) -> bool:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(
                "https://api.cas.chat/check", params={"user_id": user_id}
            ) as resp:
                data = await resp.json()
                return bool(data.get("ok"))
    except Exception:
        return False


class AntiSpamMiddleware(BaseMiddleware):
    """Удаляет рекламу/ссылки/сообщения от забаненных в CAS и выдаёт предупреждения."""

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
        if not await settings.get_bool("antispam_enabled"):
            return await handler(event, data)

        text = event.text or event.caption or ""
        if text.startswith("/"):
            return await handler(event, data)

        violation: str | None = None
        is_hard_ban = False

        if contains_banned_word(text, await settings.get_list("ban_words")):
            violation = "наркошоп/рассылка/спам (мгновенный бан)"
            is_hard_ban = True
        elif await settings.get_bool("cas_check_enabled") and await _is_cas_banned(event.from_user.id):
            violation = "числится в CAS (Combot Anti-Spam) бан-листе"
            is_hard_ban = True
        elif await settings.get_bool("delete_links") and contains_link(text):
            links = extract_links(text)
            whitelist = await settings.get_list("whitelist_domains")
            if not all(is_whitelisted(link, whitelist) for link in links):
                violation = "ссылка/реклама в сообщении"
        elif contains_banned_word(text, await settings.get_list("banned_words")):
            violation = "запрещённое слово (похоже на рекламу)"

        if violation is None:
            return await handler(event, data)

        bot: Bot = data["bot"]
        await safe_delete(bot, event.chat.id, event.message_id)
        label = f"@{event.from_user.username}" if event.from_user.username else event.from_user.full_name
        uid = event.from_user.id

        if is_hard_ban:
            if await ban_user(bot, event.chat.id, uid):
                await punish_log(
                    bot, config.log_chat_id, f"🚫 Забанен {label}: {violation}",
                    action="ban", chat_id=event.chat.id, user_id=uid, label=label,
                )
            else:
                await notify_admins(
                    bot,
                    f"⚠️ {label} нарушает ({violation}), но бот не смог забанить — "
                    f"вероятно, это администратор чата. Снимите с него права.",
                )
            return None

        warn_limit = await settings.get_int("warn_limit", config.warn_limit)
        count = await db.add_warn(event.chat.id, uid)
        await log_action(
            bot,
            config.log_chat_id,
            f"⚠️ {label}: {violation} (предупреждение {count}/{warn_limit})",
        )
        if count >= warn_limit:
            if await ban_user(bot, event.chat.id, uid):
                await db.reset_warns(event.chat.id, uid)
                await punish_log(
                    bot, config.log_chat_id, f"🚫 {label} забанен: превышен лимит предупреждений",
                    action="ban", chat_id=event.chat.id, user_id=uid, label=label,
                )
            else:
                await notify_admins(
                    bot,
                    f"⚠️ {label} превысил лимит предупреждений, но бот не смог забанить — "
                    f"вероятно, это администратор чата.",
                )
        return None
