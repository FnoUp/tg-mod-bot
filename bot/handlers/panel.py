"""Админ-панель в личке бота — навигируемое меню с разделами.

Доступна только пользователям из ADMIN_IDS. Одно сообщение-панель редактируется
на месте: из любого раздела можно вернуться в меню кнопкой, ничего не теряется и
не нужно листать вверх. Все настройки применяются сразу, без рестарта.
"""
import html

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import settings_store as settings
from bot.config import config
from bot.utils.moderation import safe_delete

router = Router(name="panel")
router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(config.admin_ids))
router.callback_query.filter(F.from_user.id.in_(config.admin_ids))


class Editing(StatesGroup):
    value = State()


# Редактируемые поля: key -> (заголовок, тип, раздел-возврата, подсказка)
FIELDS: dict[str, tuple[str, str, str, str]] = {
    "ban_preset_2": ("Текст /ban 2", "text", "texts",
                     "{user} — подставится упоминание нарушителя."),
    "check_template": ("Текст /check", "text", "texts",
                       "{time} — подставится дедлайн (сейчас +2ч по Перми)."),
    "restrict_message": ("Текст «Ограничить»", "text", "texts",
                         "Публикуется при нажатии «Ограничить доступ». {user} — упоминание."),
    "ban_words": ("Слова мгновенного бана", "list", "words_ban",
                  "Через запятую. За любое слово — мгновенный бан."),
    "banned_words": ("Слова-предупреждения", "list", "words_warn",
                     "Через запятую. За слово — предупреждение (бан по лимиту)."),
    "whitelist_domains": ("Белые домены", "list", "filters",
                          "Через запятую. Ссылки на эти домены не удаляются."),
    "warn_limit": ("Предупреждений до бана", "int", "limits", "Целое число ≥ 1."),
    "duplicate_limit": ("Лимит одинаковых сообщений", "int", "limits", "Целое число ≥ 1."),
}

# Тумблеры: key -> (заголовок, раздел)
TOGGLES: dict[str, tuple[str, str]] = {
    "antispam_enabled": ("Антиспам (реклама/слова)", "modules"),
    "antiflood_enabled": ("Антифлуд (частые сообщения)", "modules"),
    "antiraid_enabled": ("Антирейд (массовый вход)", "modules"),
    "antidup_enabled": ("Лимит повторов", "modules"),
    "delete_links": ("Удалять ссылки", "filters"),
    "cas_check_enabled": ("CAS-проверка спамеров", "filters"),
}


def _esc(text: str) -> str:
    return html.escape(text or "(пусто)")


def _short(text: str, limit: int = 180) -> str:
    text = text or "(пусто)"
    return text if len(text) <= limit else text[:limit] + "…"


def _flag(value: bool) -> str:
    return "🟢 вкл" if value else "🔴 выкл"


def _back_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:main")]


# --- Рендеры разделов: возвращают (текст, клавиатура) ---

async def render_main() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "⚙️ <b>Панель модерации</b>\n\n"
        "Выбери раздел. Изменения применяются сразу, без перезапуска бота."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Тексты", callback_data="m:texts")],
        [InlineKeyboardButton(text="🎚 Модули (вкл/выкл)", callback_data="m:modules")],
        [InlineKeyboardButton(text="⛔ Слова: мгновенный бан", callback_data="m:words_ban")],
        [InlineKeyboardButton(text="⚠️ Слова: предупреждение", callback_data="m:words_warn")],
        [InlineKeyboardButton(text="🔗 Фильтры ссылок/спама", callback_data="m:filters")],
        [InlineKeyboardButton(text="🔢 Лимиты", callback_data="m:limits")],
    ])
    return text, kb


async def render_texts() -> tuple[str, InlineKeyboardMarkup]:
    ban2 = _esc(_short(await settings.get("ban_preset_2")))
    check = _esc(_short(await settings.get("check_template")))
    restrict = _esc(_short(await settings.get("restrict_message")))
    text = (
        "📝 <b>Тексты</b>\n\n"
        f"<b>/ban 2:</b>\n{ban2}\n\n"
        f"<b>/check:</b>\n{check}\n\n"
        f"<b>«Ограничить»:</b>\n{restrict}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Изменить /ban 2", callback_data="e:ban_preset_2:texts")],
        [InlineKeyboardButton(text="✍️ Изменить /check", callback_data="e:check_template:texts")],
        [InlineKeyboardButton(text="✍️ Изменить «Ограничить»", callback_data="e:restrict_message:texts")],
        _back_row(),
    ])
    return text, kb


async def render_modules() -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    for key, (title, _menu) in TOGGLES.items():
        if _menu != "modules":
            continue
        flag = _flag(await settings.get_bool(key))
        rows.append([InlineKeyboardButton(text=f"{title} — {flag}", callback_data=f"t:{key}:modules")])
    rows.append(_back_row())
    text = "🎚 <b>Модули</b>\n\nНажми на модуль, чтобы включить или выключить его."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_wordlist(key: str, title: str, note: str, menu: str) -> tuple[str, InlineKeyboardMarkup]:
    words = await settings.get_list(key)
    listing = _esc(", ".join(words)) if words else "(список пуст)"
    text = f"{title}\n\n<b>Сейчас ({len(words)}):</b>\n{listing}\n\n{note}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Изменить список", callback_data=f"e:{key}:{menu}")],
        _back_row(),
    ])
    return text, kb


async def render_words_ban() -> tuple[str, InlineKeyboardMarkup]:
    return await _render_wordlist(
        "ban_words", "⛔ <b>Слова мгновенного бана</b>",
        "За любое из этих слов пользователь банится сразу.", "words_ban",
    )


async def render_words_warn() -> tuple[str, InlineKeyboardMarkup]:
    return await _render_wordlist(
        "banned_words", "⚠️ <b>Слова-предупреждения</b>",
        "За такое слово выдаётся предупреждение (бан по достижении лимита).", "words_warn",
    )


async def render_filters() -> tuple[str, InlineKeyboardMarkup]:
    links = _flag(await settings.get_bool("delete_links"))
    cas = _flag(await settings.get_bool("cas_check_enabled"))
    whitelist = await settings.get_list("whitelist_domains")
    wl = _esc(", ".join(whitelist)) if whitelist else "(пусто)"
    text = (
        "🔗 <b>Фильтры</b>\n\n"
        f"<b>Белые домены</b> (ссылки не удаляются):\n{wl}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔗 Удалять ссылки — {links}", callback_data="t:delete_links:filters")],
        [InlineKeyboardButton(text=f"🌐 CAS-проверка — {cas}", callback_data="t:cas_check_enabled:filters")],
        [InlineKeyboardButton(text="✍️ Изменить белые домены", callback_data="e:whitelist_domains:filters")],
        _back_row(),
    ])
    return text, kb


async def render_limits() -> tuple[str, InlineKeyboardMarkup]:
    warn = await settings.get_int("warn_limit", config.warn_limit)
    dup = await settings.get_int("duplicate_limit", config.duplicate_limit)
    text = (
        "🔢 <b>Лимиты</b>\n\n"
        f"⚠️ Предупреждений до бана: <b>{warn}</b>\n"
        f"♻️ Одинаковых сообщений подряд: <b>{dup}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚠️ Предупреждений до бана: {warn}", callback_data="e:warn_limit:limits")],
        [InlineKeyboardButton(text=f"♻️ Лимит повторов: {dup}", callback_data="e:duplicate_limit:limits")],
        _back_row(),
    ])
    return text, kb


MENUS = {
    "main": render_main,
    "texts": render_texts,
    "modules": render_modules,
    "words_ban": render_words_ban,
    "words_warn": render_words_warn,
    "filters": render_filters,
    "limits": render_limits,
}


async def render(menu: str) -> tuple[str, InlineKeyboardMarkup]:
    return await MENUS.get(menu, render_main)()


# --- Хендлеры ---

@router.message(Command("panel", "admin", "start"))
async def cmd_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_main()
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("m:"))
async def on_nav(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()  # любой переход по меню отменяет режим редактирования
    menu = callback.data.split(":", 1)[1]
    text, kb = await render(menu)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("t:"))
async def on_toggle(callback: CallbackQuery) -> None:
    _, key, menu = callback.data.split(":")
    new_value = await settings.toggle(key)
    text, kb = await render(menu)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("🟢 Включено" if new_value else "🔴 Выключено")


@router.callback_query(F.data.startswith("e:"))
async def on_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, key, menu = callback.data.split(":")
    title, kind, _menu, hint = FIELDS[key]
    current = _esc(await settings.get(key))
    await state.set_state(Editing.value)
    await state.update_data(
        key=key, menu=menu, kind=kind,
        mid=callback.message.message_id, cid=callback.message.chat.id,
    )
    prompt = (
        f"✏️ <b>{title}</b>\n\n"
        f"<b>Сейчас:</b>\n{current}\n\n"
        f"ℹ️ {hint}\n\n"
        f"Пришли новое значение сообщением. Отмена — кнопка ниже или /cancel."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"m:{menu}")]])
    await callback.message.edit_text(prompt, reply_markup=kb)
    await callback.answer()


async def _return_to_menu(bot: Bot, cid: int, mid: int, menu: str, prefix: str = "") -> None:
    text, kb = await render(menu)
    try:
        await bot.edit_message_text(chat_id=cid, message_id=mid, text=prefix + text, reply_markup=kb)
    except Exception:
        await bot.send_message(cid, prefix + text, reply_markup=kb)


@router.message(Editing.value, Command("cancel"))
async def on_cancel(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await safe_delete(bot, message.chat.id, message.message_id)
    await _return_to_menu(bot, data["cid"], data["mid"], data.get("menu", "main"))


@router.message(Editing.value, F.text)
async def on_edit_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    key, menu, kind = data["key"], data["menu"], data["kind"]
    raw = message.text

    if kind == "int":
        cleaned = raw.strip()
        if not cleaned.isdigit() or int(cleaned) < 1:
            await message.reply("Нужно целое число ≥ 1. Пришли ещё раз или /cancel.")
            return
        value = str(int(cleaned))
    elif kind == "list":
        items = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]
        value = ",".join(items)
    else:  # text — сохраняем как есть, с переносами строк
        value = raw

    await settings.set(key, value)
    await state.clear()
    await safe_delete(bot, message.chat.id, message.message_id)
    await _return_to_menu(bot, data["cid"], data["mid"], menu, prefix="✅ <b>Сохранено.</b>\n\n")
