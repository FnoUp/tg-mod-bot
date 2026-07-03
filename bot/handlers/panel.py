"""Админ-панель в личке бота — навигируемое меню с разделами.

Доступна только пользователям из ADMIN_IDS. Одно сообщение-панель редактируется
на месте: из любого раздела можно вернуться в меню кнопкой, ничего не теряется и
не нужно листать вверх. Все настройки применяются сразу, без рестарта.
"""
import html
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import database as db
from bot import settings_store as settings
from bot.config import config
from bot.filters.admin import IsBotAdmin
from bot.utils import access
from bot.utils.moderation import safe_delete

PERM_TZ = timezone(timedelta(hours=5))  # Пермь = UTC+5

router = Router(name="panel")
# Доступ к панели — базовые (.env) + добавленные из меню админы
router.message.filter(F.chat.type == ChatType.PRIVATE, IsBotAdmin())
router.callback_query.filter(IsBotAdmin())


class Editing(StatesGroup):
    value = State()


class AddAdmin(StatesGroup):
    value = State()


# Редактируемые поля: key -> (заголовок, тип, раздел-возврата, подсказка)
FIELDS: dict[str, tuple[str, str, str, str]] = {
    "ban_preset_2": ("Текст /ban 2", "text", "texts",
                     "{user} — подставится упоминание нарушителя."),
    "check_template": ("Текст /check", "text", "texts",
                       "{time} — подставится дедлайн (сейчас +2ч по Перми)."),
    "restrict_message": ("Текст «Ограничить»", "text", "texts",
                         "Публикуется при нажатии «Ограничить доступ». {user} — упоминание."),
    "welcome_text": ("Приветствие новичка", "text", "texts",
                     "Отправляется после прохождения капчи. {user} — упоминание."),
    "dup_warn_text": ("Предупреждение о повторах", "text", "texts",
                      "Пишется нарушителю в чат раз в 24 ч. {user} — упоминание."),
    "ban_words": ("Слова мгновенного бана", "list", "words_ban",
                  "Через запятую. За любое слово — мгновенный бан."),
    "banned_words": ("Слова-предупреждения", "list", "words_warn",
                     "Через запятую. За слово — предупреждение (бан по лимиту)."),
    "whitelist_domains": ("Белые домены", "list", "filters",
                          "Через запятую. Ссылки на эти домены не удаляются."),
    "warn_limit": ("Предупреждений до бана", "int", "limits", "Целое число ≥ 1."),
    "duplicate_limit": ("Лимит одинаковых сообщений", "int", "limits", "Целое число ≥ 1."),
    "check_offset_hours": ("Дедлайн /check, часов", "int", "limits",
                           "Через сколько часов ставить время в тексте /check. Целое ≥ 1."),
    "flood_message_limit": ("Флуд: сообщений", "int", "limits",
                            "Сколько сообщений подряд разрешено. Целое ≥ 1."),
    "flood_interval_seconds": ("Флуд: за секунд", "int", "limits",
                               "За какое окно (секунд) считать сообщения. Целое ≥ 1."),
    "flood_mute_minutes": ("Флуд: мьют минут", "int", "limits",
                           "На сколько минут мьютить за флуд. Целое ≥ 1."),
}

# Тумблеры: key -> (заголовок, раздел)
TOGGLES: dict[str, tuple[str, str]] = {
    "antispam_enabled": ("Антиспам (реклама/слова)", "modules"),
    "antiflood_enabled": ("Антифлуд (частые сообщения)", "modules"),
    "antiraid_enabled": ("Антирейд (массовый вход)", "modules"),
    "antidup_enabled": ("Лимит повторов", "modules"),
    "welcome_enabled": ("Приветствие новичков", "modules"),
    "notify_first_msg_enabled": ("Уведомлять о первом сообщении", "modules"),
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
        [InlineKeyboardButton(text="♻️ Вернуть пользователя", callback_data="m:restore")],
        [InlineKeyboardButton(text="📊 Статистика за неделю", callback_data="m:stats")],
        [InlineKeyboardButton(text="🕓 История действий", callback_data="m:history")],
        [InlineKeyboardButton(text="👑 Админы бота", callback_data="m:admins")],
    ])
    return text, kb


async def render_texts() -> tuple[str, InlineKeyboardMarkup]:
    ban2 = _esc(_short(await settings.get("ban_preset_2")))
    check = _esc(_short(await settings.get("check_template")))
    restrict = _esc(_short(await settings.get("restrict_message")))
    welcome = _esc(_short(await settings.get("welcome_text")))
    dupwarn = _esc(_short(await settings.get("dup_warn_text")))
    text = (
        "📝 <b>Тексты</b>\n\n"
        f"<b>/ban 2:</b>\n{ban2}\n\n"
        f"<b>/check:</b>\n{check}\n\n"
        f"<b>«Ограничить»:</b>\n{restrict}\n\n"
        f"<b>Приветствие:</b>\n{welcome}\n\n"
        f"<b>Предупреждение о повторах:</b>\n{dupwarn}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Изменить /ban 2", callback_data="e:ban_preset_2:texts")],
        [InlineKeyboardButton(text="✍️ Изменить /check", callback_data="e:check_template:texts")],
        [InlineKeyboardButton(text="✍️ Изменить «Ограничить»", callback_data="e:restrict_message:texts")],
        [InlineKeyboardButton(text="✍️ Изменить приветствие", callback_data="e:welcome_text:texts")],
        [InlineKeyboardButton(text="✍️ Изменить предупр. о повторах", callback_data="e:dup_warn_text:texts")],
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
    text = (
        "🎚 <b>Модули</b>\n\n"
        "🛡 <b>Антиспам</b> — удаляет ссылки и стоп-слова; за слово из «мгновенного» "
        "списка сразу банит (тихо, без сообщений в чат).\n\n"
        "🚨 <b>Антифлуд</b> — если человек шлёт сообщения слишком часто, временно "
        "лишает его права писать (мьют).\n\n"
        "🚪 <b>Антирейд</b> — при массовом входе людей за короткое время автоматически "
        "ограничивает новичков и присылает тебе тревогу.\n\n"
        "♻️ <b>Лимит повторов</b> — не даёт постить одно и то же сообщение больше "
        "заданного числа раз (борьба с рекламщиками).\n\n"
        "👋 <b>Приветствие новичков</b> — после прохождения капчи присылает "
        "новичку приветствие и правила (текст — в разделе «Тексты»).\n\n"
        "🆕 <b>Уведомлять о первом сообщении</b> — присылает тебе в ЛС, когда "
        "<b>только что вступивший</b> участник впервые пишет в чат (с текстом). "
        "Про старых участников не уведомляет.\n\n"
        "Нажми на модуль, чтобы включить или выключить его."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_wordlist(key: str, title: str, note: str, menu: str) -> tuple[str, InlineKeyboardMarkup]:
    words = await settings.get_list(key)
    listing = _esc(", ".join(words)) if words else "(список пуст)"
    text = f"{title}\n\n<b>Сейчас ({len(words)}):</b>\n{listing}\n\n{note}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить слова", callback_data=f"e:{key}:{menu}:add")],
        [InlineKeyboardButton(text="✏️ Редактировать всё", callback_data=f"e:{key}:{menu}:rep")],
        [InlineKeyboardButton(text="🗑 Очистить список", callback_data=f"clr:{key}:{menu}")],
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
    check_h = await settings.get_int("check_offset_hours", 1)
    fmsg = await settings.get_int("flood_message_limit", config.flood_message_limit)
    fint = await settings.get_int("flood_interval_seconds", config.flood_interval_seconds)
    fmute = await settings.get_int("flood_mute_minutes", config.flood_mute_minutes)
    text = (
        "🔢 <b>Лимиты</b>\n\n"
        f"⚠️ Предупреждений до бана: <b>{warn}</b>\n"
        f"♻️ Одинаковых сообщений подряд: <b>{dup}</b>\n"
        f"⏱ Дедлайн /check: <b>+{check_h} ч</b>\n\n"
        f"🚨 <b>Антифлуд:</b> мьют, если больше <b>{fmsg}</b> сообщений "
        f"за <b>{fint}</b> сек → мьют на <b>{fmute}</b> мин."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚠️ Предупреждений до бана: {warn}", callback_data="e:warn_limit:limits")],
        [InlineKeyboardButton(text=f"♻️ Лимит повторов: {dup}", callback_data="e:duplicate_limit:limits")],
        [InlineKeyboardButton(text=f"⏱ Дедлайн /check: +{check_h} ч", callback_data="e:check_offset_hours:limits")],
        [InlineKeyboardButton(text=f"🚨 Флуд: сообщений {fmsg}", callback_data="e:flood_message_limit:limits")],
        [InlineKeyboardButton(text=f"🚨 Флуд: за секунд {fint}", callback_data="e:flood_interval_seconds:limits")],
        [InlineKeyboardButton(text=f"🚨 Флуд: мьют минут {fmute}", callback_data="e:flood_mute_minutes:limits")],
        _back_row(),
    ])
    return text, kb


HISTORY_PER_PAGE = 10
HISTORY_KINDS = {"all": "Все", "ban": "Баны", "mute": "Мьюты"}


def _filter_button(kind: str, current: str) -> InlineKeyboardButton:
    title = HISTORY_KINDS[kind]
    label = f"• {title} •" if kind == current else title
    return InlineKeyboardButton(text=label, callback_data=f"hp:{kind}:0")


async def render_history(kind: str = "all", page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    if kind not in HISTORY_KINDS:
        kind = "all"
    total = await db.count_logs(kind)
    pages = max(1, (total + HISTORY_PER_PAGE - 1) // HISTORY_PER_PAGE)
    page = max(0, min(page, pages - 1))
    entries = await db.get_logs_page(HISTORY_PER_PAGE, page * HISTORY_PER_PAGE, kind)

    if not entries:
        body = "Пусто."
    else:
        lines = []
        for ts, entry in entries:
            when = datetime.fromtimestamp(ts, PERM_TZ).strftime("%d.%m %H:%M")
            lines.append(f"🕗 <b>{when}</b>\n{_esc(entry)}")
        body = "\n\n".join(lines)  # пустая строка между записями

    text = (
        f"🕓 <b>История</b> — {HISTORY_KINDS[kind]}, стр. {page + 1}/{pages}, "
        f"всего {total} (время — Пермь)\n\n" + body
    )

    filter_row = [_filter_button(k, kind) for k in HISTORY_KINDS]

    # Навигация: [⏮][◀ N][· тек/всего ·][N ▶][⏭]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⏮", callback_data=f"hp:{kind}:0"))
        nav.append(InlineKeyboardButton(text=f"◀ {page}", callback_data=f"hp:{kind}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"· {page + 1}/{pages} ·", callback_data=f"hp:{kind}:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text=f"{page + 2} ▶", callback_data=f"hp:{kind}:{page + 1}"))
        nav.append(InlineKeyboardButton(text="⏭", callback_data=f"hp:{kind}:{pages - 1}"))

    rows = [filter_row, nav,
            [InlineKeyboardButton(text="🗑 Очистить историю", callback_data="hclr:ask")],
            _back_row()]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_restore() -> tuple[str, InlineKeyboardMarkup]:
    bans = await db.get_recent_actions("ban", 8)
    mutes = await db.get_recent_actions("mute", 8)
    rows: list[list[InlineKeyboardButton]] = []
    text = "♻️ <b>Восстановление доступа</b>\n\n"
    if bans:
        text += "<b>Недавно забаненные</b> — нажми, чтобы разбанить и получить ссылку для возврата:\n"
        for chat_id, user_id, label, _ts in bans:
            rows.append([InlineKeyboardButton(
                text=f"↩️ Разбан {label}", callback_data=f"undo:ban:{chat_id}:{user_id}")])
    if mutes:
        text += "\n<b>Недавно ограниченные</b> — нажми, чтобы снять мьют:\n"
        for chat_id, user_id, label, _ts in mutes:
            rows.append([InlineKeyboardButton(
                text=f"🔊 Снять мьют {label}", callback_data=f"undo:mute:{chat_id}:{user_id}")])
    if not bans and not mutes:
        text += "Пока некого восстанавливать — бот никого не банил и не мьютил."
    rows.append(_back_row())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_stats() -> tuple[str, InlineKeyboardMarkup]:
    now = int(time.time())
    week, day = now - 7 * 86400, now - 86400
    b7 = await db.count_actions_since("ban", week)
    m7 = await db.count_actions_since("mute", week)
    k7 = await db.count_actions_since("kick", week)
    b1 = await db.count_actions_since("ban", day)
    m1 = await db.count_actions_since("mute", day)
    text = (
        "📊 <b>Статистика</b>\n\n"
        "<b>За неделю:</b>\n"
        f"🚫 Банов: <b>{b7}</b>\n"
        f"🔇 Мьютов: <b>{m7}</b>\n"
        f"👢 Киков: <b>{k7}</b>\n\n"
        "<b>За сутки:</b>\n"
        f"🚫 Банов: <b>{b1}</b>\n"
        f"🔇 Мьютов: <b>{m1}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="m:stats")],
        _back_row(),
    ])
    return text, kb


async def render_admins() -> tuple[str, InlineKeyboardMarkup]:
    env = access.get_env_admins()
    extra = access.get_extra_admins()
    text = (
        "👑 <b>Админы бота</b>\n\n"
        "У них есть доступ к этой панели, к командам в чате и иммунитет от модерации.\n\n"
        f"<b>Базовые</b> (из .env, убрать нельзя):\n{', '.join(map(str, env)) or '—'}\n\n"
        f"<b>Добавленные</b>:\n{', '.join(map(str, extra)) or 'нет'}"
    )
    rows = [[InlineKeyboardButton(text="➕ Добавить админа", callback_data="adm_add")]]
    for uid in extra:
        rows.append([InlineKeyboardButton(text=f"🗑 Убрать {uid}", callback_data=f"adm_del:{uid}")])
    rows.append(_back_row())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


MENUS = {
    "main": render_main,
    "texts": render_texts,
    "modules": render_modules,
    "words_ban": render_words_ban,
    "words_warn": render_words_warn,
    "filters": render_filters,
    "limits": render_limits,
    "restore": render_restore,
    "stats": render_stats,
    "history": render_history,
    "admins": render_admins,
}


async def render(menu: str) -> tuple[str, InlineKeyboardMarkup]:
    return await MENUS.get(menu, render_main)()


# --- Хендлеры ---

@router.message(Command("panel", "admin", "start"))
async def cmd_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_main()
    await message.answer(text, reply_markup=kb)


async def _safe_edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    # Игнорируем "message is not modified" при обновлении того же экрана
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data.startswith("m:"))
async def on_nav(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()  # любой переход по меню отменяет режим редактирования
    menu = callback.data.split(":", 1)[1]
    text, kb = await render(menu)
    await _safe_edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == "openmenu")
async def on_open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_main()
    # Новым сообщением снизу, чтобы не терять уведомление и не «улетало» меню
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("hp:"))
async def on_history_page(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, kind, page = callback.data.split(":")
    text, kb = await render_history(kind, int(page))
    await _safe_edit(callback, text, kb)
    await callback.answer()


@router.callback_query(F.data == "hclr:ask")
async def on_history_clear_ask(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, очистить", callback_data="hclr:yes")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="hp:all:0")],
    ])
    await _safe_edit(callback, "🗑 <b>Очистить всю историю действий?</b>\nЭто удалит все записи безвозвратно.", kb)
    await callback.answer()


@router.callback_query(F.data == "hclr:yes")
async def on_history_clear_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await db.clear_logs()
    text, kb = await render_history("all", 0)
    await _safe_edit(callback, "✅ <b>История очищена.</b>\n\n" + text, kb)
    await callback.answer("Очищено")


# --- Управление админами бота ---

@router.callback_query(F.data == "adm_add")
async def on_admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAdmin.value)
    await state.update_data(mid=callback.message.message_id, cid=callback.message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="m:admins")]])
    await callback.message.edit_text(
        "👑 <b>Добавить админа</b>\n\n"
        "Пришли <b>Telegram ID</b> пользователя (только цифры).\n"
        "Как узнать: пусть человек напишет боту @userinfobot, или ответь на его "
        "сообщение в чате командой /id.\n\nОтмена — кнопка ниже или /cancel.",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(AddAdmin.value, Command("cancel"))
async def on_admin_add_cancel(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    await safe_delete(bot, message.chat.id, message.message_id)
    await _return_to_menu(bot, data["cid"], data["mid"], "admins")


@router.message(AddAdmin.value, F.text)
async def on_admin_add_value(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    val = message.text.strip()
    if not val.lstrip("-").isdigit():
        await message.reply("Нужен числовой Telegram ID. Пришли ещё раз или /cancel.")
        return
    uid = int(val)
    ids = await settings.get_list("extra_admins")
    if str(uid) not in ids:
        ids.append(str(uid))
        await settings.set("extra_admins", ",".join(ids))
    access.add_extra_admin(uid)
    await state.clear()
    await safe_delete(bot, message.chat.id, message.message_id)
    await _return_to_menu(bot, data["cid"], data["mid"], "admins", prefix=f"✅ <b>Админ {uid} добавлен.</b>\n\n")


@router.callback_query(F.data.startswith("adm_del:"))
async def on_admin_del(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = int(callback.data.split(":", 1)[1])
    ids = [x for x in await settings.get_list("extra_admins") if x != str(uid)]
    await settings.set("extra_admins", ",".join(ids))
    access.remove_extra_admin(uid)
    text, kb = await render_admins()
    await _safe_edit(callback, f"✅ <b>Админ {uid} убран.</b>\n\n" + text, kb)
    await callback.answer("Убрано")


@router.callback_query(F.data.startswith("t:"))
async def on_toggle(callback: CallbackQuery) -> None:
    _, key, menu = callback.data.split(":")
    new_value = await settings.toggle(key)
    text, kb = await render(menu)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("🟢 Включено" if new_value else "🔴 Выключено")


@router.callback_query(F.data.startswith("e:"))
async def on_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    key, menu = parts[1], parts[2]
    mode = parts[3] if len(parts) > 3 else "rep"  # add | rep
    title, kind, _menu, hint = FIELDS[key]
    current = _esc(await settings.get(key))
    await state.set_state(Editing.value)
    await state.update_data(
        key=key, menu=menu, kind=kind, mode=mode,
        mid=callback.message.message_id, cid=callback.message.chat.id,
    )
    if kind == "list" and mode == "add":
        instr = "Пришли слова через запятую — они <b>добавятся</b> к текущим, старые сохранятся."
    elif kind == "list":
        instr = "Пришли <b>полный новый</b> список через запятую — старый будет заменён."
    else:
        instr = "Пришли новое значение сообщением."
    prompt = (
        f"✏️ <b>{title}</b>\n\n"
        f"<b>Сейчас:</b>\n{current}\n\n"
        f"ℹ️ {hint}\n\n"
        f"{instr}\nОтмена — кнопка ниже или /cancel."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"m:{menu}")]])
    await callback.message.edit_text(prompt, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("clr:"))
async def on_clear(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, key, menu = callback.data.split(":")
    await settings.set(key, "")
    text, kb = await render(menu)
    await callback.message.edit_text("✅ <b>Список очищен.</b>\n\n" + text, reply_markup=kb)
    await callback.answer("Очищено")


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
        if data.get("mode") == "add":
            existing = await settings.get_list(key)
            seen = {w.lower() for w in existing}
            merged = existing + [w for w in items if w.lower() not in seen]
            value = ",".join(merged)
        else:
            value = ",".join(items)
    else:  # text — сохраняем как есть, с переносами строк
        value = raw

    await settings.set(key, value)
    await state.clear()
    await safe_delete(bot, message.chat.id, message.message_id)
    await _return_to_menu(bot, data["cid"], data["mid"], menu, prefix="✅ <b>Сохранено.</b>\n\n")
