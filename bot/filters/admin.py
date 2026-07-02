from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.utils.access import is_bot_admin


class IsBotAdmin(BaseFilter):
    """Пропускает только доверенных админов бота (базовые + добавленные).

    Работает и для Message, и для CallbackQuery — оба имеют from_user."""

    async def __call__(self, event) -> bool:
        user = getattr(event, "from_user", None)
        return is_bot_admin(user.id if user else None)


class IsChatOwner(BaseFilter):
    """Команды в чате доступны только владельцу (создателю), админам бота
    (ADMIN_IDS) и при отправке «от имени сообщества/канала».

    Обычные админы чата (в т.ч. рекламщики, которым выдали админку) — НЕ проходят."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        # Сообщение от имени сообщества/канала (анонимный админ, владелец «от сообщества»)
        if message.sender_chat is not None:
            return True
        user = message.from_user
        if user is None:
            return False
        if is_bot_admin(user.id):
            return True
        try:
            member = await bot.get_chat_member(message.chat.id, user.id)
            return member.status == ChatMemberStatus.CREATOR
        except Exception:
            return False
