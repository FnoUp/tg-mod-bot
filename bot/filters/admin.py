from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.utils.access import is_bot_admin


class IsBotAdmin(BaseFilter):
    """Пропускает только доверенных админов бота (ADMIN_IDS)."""

    async def __call__(self, message: Message) -> bool:
        return is_bot_admin(message.from_user.id if message.from_user else None)
