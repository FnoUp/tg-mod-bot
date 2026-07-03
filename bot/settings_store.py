"""Редактируемые из бота настройки. Хранятся в БД, кэшируются в памяти.

Значения по умолчанию берутся из .env (config) при первом запуске, дальше
редактируются прямо из админ-панели без рестарта контейнера.
"""
from bot import database as db
from bot.config import config

CHECK_TEMPLATE_DEFAULT = (
    "Прошу прочтите закрепленное сообщение сверху\U0001f446\n\n"
    "❗️У вас один час (до {time} по Пермскому времени) для выполнения условий!❗️\n"
    "‼️В противном случае придётся ограничить ваши возможности в этом чате!‼️\n"
    "\U0001f504В случае смены аккаунта, если вы уже проходили проверку, просьба так же "
    "подтвердить личность, чтобы исключить возможные фейки!\U0001f504"
)

BAN_PRESET_2_DEFAULT = (
    "{user}\n"
    "Заблокирован\n"
    "Причина блокировки: Развод / предоплата / агенство / наркотики"
)

RESTRICT_MESSAGE_DEFAULT = (
    "{user}\n"
    "‼️К сожалению вы не предоставили данные для проверки, ваши действия в этой группе ограничены.‼️\n\n"
    "✅Вы всегда можете снять ограничение, если пройдете проверку.\n\n"
    "Вся информация в закреплённом сообщении сверху\U0001f446"
)

WELCOME_TEXT_DEFAULT = (
    "\U0001f44b {user}, добро пожаловать!\n\n"
    "Пожалуйста, ознакомьтесь с правилами в закреплённом сообщении.\n"
    "Реклама, спам и оскорбления запрещены — за нарушение бан."
)

DUP_WARN_TEXT_DEFAULT = (
    "{user}, нельзя отправлять одно и то же сообщение больше {limit} раз в сутки "
    "(счётчик обнуляется в 00:00 по Перми). Повторы удаляются, нарушителям — мут на сутки."
)

# Значения по умолчанию. Списки хранятся строкой через запятую.
DEFAULTS: dict[str, str] = {
    # Тексты
    "check_template": CHECK_TEMPLATE_DEFAULT,
    "ban_preset_2": BAN_PRESET_2_DEFAULT,
    "restrict_message": RESTRICT_MESSAGE_DEFAULT,
    "welcome_text": WELCOME_TEXT_DEFAULT,
    "dup_warn_text": DUP_WARN_TEXT_DEFAULT,
    # Модули (вкл/выкл)
    "antispam_enabled": "true",
    "antiflood_enabled": "true",
    "antiraid_enabled": "true",
    "antidup_enabled": "true",
    "welcome_enabled": "true",
    "notify_first_msg_enabled": "true",
    # Фильтры
    "delete_links": "true" if config.delete_links else "false",
    "cas_check_enabled": "true" if config.cas_check_enabled else "false",
    # Списки слов и доменов
    "ban_words": ",".join(config.ban_words),
    "banned_words": ",".join(config.banned_words),
    "whitelist_domains": ",".join(config.whitelist_domains),
    # Числовые лимиты
    "warn_limit": str(config.warn_limit),
    "duplicate_limit": str(config.duplicate_limit),
    "check_offset_hours": "1",
    "flood_message_limit": str(config.flood_message_limit),
    "flood_interval_seconds": str(config.flood_interval_seconds),
    "flood_mute_minutes": str(config.flood_mute_minutes),
    # Добавленные из панели админы бота (ID через запятую)
    "extra_admins": "",
}

_cache: dict[str, str] = {}


async def get(key: str) -> str:
    if key in _cache:
        return _cache[key]
    value = await db.get_setting(key)
    if value is None:
        value = DEFAULTS.get(key, "")
    _cache[key] = value
    return value


async def set(key: str, value: str) -> None:
    await db.set_setting(key, value)
    _cache[key] = value


async def get_bool(key: str) -> bool:
    return (await get(key)).strip().lower() == "true"


async def toggle(key: str) -> bool:
    new_value = not await get_bool(key)
    await set(key, "true" if new_value else "false")
    return new_value


async def get_list(key: str) -> list[str]:
    return [item.strip() for item in (await get(key)).split(",") if item.strip()]


async def get_int(key: str, fallback: int = 0) -> int:
    try:
        return int((await get(key)).strip())
    except (ValueError, AttributeError):
        try:
            return int(DEFAULTS.get(key, fallback))
        except ValueError:
            return fallback
