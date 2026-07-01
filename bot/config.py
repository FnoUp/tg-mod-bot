import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_set(raw: str) -> set[int]:
    return {int(x) for x in raw.split(",") if x.strip()}


def _str_list(raw: str) -> list[str]:
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


@dataclass
class Config:
    bot_token: str
    admin_ids: set[int]
    log_chat_id: int | None
    db_path: str
    flood_message_limit: int
    flood_interval_seconds: int
    flood_mute_minutes: int
    warn_limit: int
    captcha_timeout_seconds: int
    raid_join_limit: int
    raid_window_seconds: int
    raid_lockdown_minutes: int
    duplicate_limit: int
    duplicate_window_hours: int
    duplicate_min_length: int
    cas_check_enabled: bool
    delete_links: bool
    banned_words: list[str]
    ban_words: list[str]
    whitelist_domains: list[str]


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан (см. .env.example)")

    log_chat_raw = os.getenv("LOG_CHAT_ID", "").strip()

    return Config(
        bot_token=token,
        admin_ids=_int_set(os.getenv("ADMIN_IDS", "")),
        log_chat_id=int(log_chat_raw) if log_chat_raw else None,
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        flood_message_limit=int(os.getenv("FLOOD_MESSAGE_LIMIT", "5")),
        flood_interval_seconds=int(os.getenv("FLOOD_INTERVAL_SECONDS", "10")),
        flood_mute_minutes=int(os.getenv("FLOOD_MUTE_MINUTES", "10")),
        warn_limit=int(os.getenv("WARN_LIMIT", "3")),
        captcha_timeout_seconds=int(os.getenv("CAPTCHA_TIMEOUT_SECONDS", "120")),
        raid_join_limit=int(os.getenv("RAID_JOIN_LIMIT", "5")),
        raid_window_seconds=int(os.getenv("RAID_WINDOW_SECONDS", "30")),
        raid_lockdown_minutes=int(os.getenv("RAID_LOCKDOWN_MINUTES", "10")),
        duplicate_limit=int(os.getenv("DUPLICATE_LIMIT", "3")),
        duplicate_window_hours=int(os.getenv("DUPLICATE_WINDOW_HOURS", "24")),
        duplicate_min_length=int(os.getenv("DUPLICATE_MIN_LENGTH", "10")),
        cas_check_enabled=os.getenv("CAS_CHECK_ENABLED", "true").lower() == "true",
        delete_links=os.getenv("DELETE_LINKS", "true").lower() == "true",
        banned_words=_str_list(os.getenv("BANNED_WORDS", "")),
        ban_words=_str_list(os.getenv("BAN_WORDS", "")),
        whitelist_domains=_str_list(os.getenv("WHITELIST_DOMAINS", "")),
    )


config = load_config()
