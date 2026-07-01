"""Админ-панель в личке бота. Доступна только пользователям из ADMIN_IDS.

Позволяет менять тексты пресетов бана, текст /check и включать/выключать
модули без правки .env и рестарта контейнера.
"""
from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import settings_store as settings
from bot.config import config

router = Router(name="panel")
# Панель — только в личке и только для админов из ADMIN_IDS
router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(config.admin_ids))
router.callback_query.filter(F.from_user.id.in_(config.admin_ids))


class EditText(StatesGroup):
    waiting = State()


# Ключи, которые редактируются как свободный текст, и их подписи
TEXT_FIELDS = {
    "ban_preset_1": "Текст пресета /ban 1",
    "ban_preset_2": "Текст пресета /ban 2",
    "check_template": "Текст /check (используй {time} — подставится дедлайн)",
}


def _state(value: bool) -> str:
    return "🟢 вкл" if value else "🔴 выкл"


async def _main_menu() -> InlineKeyboardMarkup:
    antispam = _state(await settings.get_bool("antispam_enabled"))
    antiraid = _state(await settings.get_bool("antiraid_enabled"))
    antidup = _state(await settings.get_bool("antidup_enabled"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Текст /ban 1", callback_data="edit:ban_preset_1")],
            [InlineKeyboardButton(text="✍️ Текст /ban 2", callback_data="edit:ban_preset_2")],
            [InlineKeyboardButton(text="✍️ Текст /check", callback_data="edit:check_template")],
            [InlineKeyboardButton(text=f"🛡 Антиспам: {antispam}", callback_data="toggle:antispam_enabled")],
            [InlineKeyboardButton(text=f"🚪 Антирейд: {antiraid}", callback_data="toggle:antiraid_enabled")],
            [
                InlineKeyboardButton(
                    text=f"♻️ Лимит повторов ({config.duplicate_limit}): {antidup}",
                    callback_data="toggle:antidup_enabled",
                )
            ],
            [InlineKeyboardButton(text="👀 Показать тексты", callback_data="show:all")],
        ]
    )


@router.message(Command("panel", "admin", "start"))
async def cmd_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "⚙️ <b>Админ-панель модерации</b>\n\n"
        "Здесь ты редактируешь тексты и включаешь/выключаешь модули. "
        "Сюда же приходят все уведомления и кнопки действий.",
        reply_markup=await _main_menu(),
    )


@router.callback_query(F.data.startswith("toggle:"))
async def on_toggle(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 1)[1]
    new_value = await settings.toggle(key)
    await callback.message.edit_reply_markup(reply_markup=await _main_menu())
    await callback.answer(f"{'Включено' if new_value else 'Выключено'}")


@router.callback_query(F.data == "show:all")
async def on_show(callback: CallbackQuery) -> None:
    lines = []
    for key, title in TEXT_FIELDS.items():
        value = await settings.get(key)
        lines.append(f"<b>{title}</b>\n{value}\n")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("edit:"))
async def on_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    current = await settings.get(key)
    await state.update_data(edit_key=key)
    await state.set_state(EditText.waiting)
    await callback.message.answer(
        f"Пришли новый текст для «{TEXT_FIELDS[key]}».\n\n"
        f"Текущий:\n{current}\n\n"
        f"Отмена — /cancel"
    )
    await callback.answer()


@router.message(Command("cancel"), EditText.waiting)
async def on_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=await _main_menu())


@router.message(EditText.waiting, F.text)
async def on_edit_finish(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("edit_key")
    await settings.set(key, message.text)
    await state.clear()
    await message.answer(
        f"✅ Сохранено: «{TEXT_FIELDS.get(key, key)}».",
        reply_markup=await _main_menu(),
    )
