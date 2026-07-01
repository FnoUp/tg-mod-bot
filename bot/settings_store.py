"""Редактируемые из бота настройки. Хранятся в БД, кэшируются в памяти.

В отличие от .env (нужен рестарт), эти значения меняются на лету через админ-панель.
"""
from bot import database as db

CHECK_TEMPLATE_DEFAULT = (
    "Прошу прочтите закрепленное сообщение сверху\U0001f446\n\n"
    "❗️У вас один час (до {time} по Пермскому времени) для выполнения условий!❗️\n"
    "‼️В противном случае придётся ограничить ваши возможности в этом чате!‼️\n"
    "\U0001f504В случае смены аккаунта, если вы уже проходили проверку, просьба так же "
    "подтвердить личность, чтобы исключить возможные фейки!\U0001f504"
)

DEFAULTS: dict[str, str] = {
    "check_template": CHECK_TEMPLATE_DEFAULT,
    "ban_preset_1": "\U0001f6ab Пользователь заблокирован за нарушение правил чата (реклама/спам).",
    "ban_preset_2": (
        "\U0001f6ab Пользователь заблокирован за запрещённый контент (наркошоп/рассылка). "
        "Информация передана администрации."
    ),
    "antispam_enabled": "true",
    "antiraid_enabled": "true",
    "antidup_enabled": "true",
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
