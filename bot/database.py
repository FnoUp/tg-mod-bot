import time
from pathlib import Path

import aiosqlite

_db: aiosqlite.Connection | None = None


async def init_db(path: str) -> None:
    global _db
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(path)
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS warns ("
        "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (chat_id, user_id))"
    )
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS captcha_pending ("
        "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id INTEGER NOT NULL, "
        "join_ts INTEGER NOT NULL, PRIMARY KEY (chat_id, user_id))"
    )
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS action_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, text TEXT NOT NULL)"
    )
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, chat_id INTEGER NOT NULL, "
        "user_id INTEGER NOT NULL, action TEXT NOT NULL, label TEXT NOT NULL)"
    )
    await _db.execute(
        "CREATE TABLE IF NOT EXISTS pending_first_msg ("
        "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (chat_id, user_id))"
    )
    await _db.commit()


async def add_pending_first(chat_id: int, user_id: int) -> None:
    """Отмечает вступившего участника — ждём его первое сообщение для уведомления."""
    await _db.execute(
        "INSERT OR IGNORE INTO pending_first_msg (chat_id, user_id) VALUES (?, ?)",
        (chat_id, user_id),
    )
    await _db.commit()


async def take_pending_first(chat_id: int, user_id: int) -> bool:
    """True, если участник числился «новым» (и снимает эту отметку)."""
    cur = await _db.execute(
        "DELETE FROM pending_first_msg WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    )
    await _db.commit()
    return cur.rowcount > 0


async def add_action(chat_id: int, user_id: int, action: str, label: str) -> None:
    await _db.execute(
        "INSERT INTO actions (ts, chat_id, user_id, action, label) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), chat_id, user_id, action, label),
    )
    await _db.execute(
        "DELETE FROM actions WHERE id NOT IN (SELECT id FROM actions ORDER BY id DESC LIMIT 1000)"
    )
    await _db.commit()


async def get_recent_actions(action: str, limit: int = 8) -> list[tuple[int, int, str, int]]:
    """Последние уникальные пользователи по типу действия: (chat_id, user_id, label, ts)."""
    async with _db.execute(
        "SELECT chat_id, user_id, label, MAX(ts) AS m FROM actions WHERE action = ? "
        "GROUP BY chat_id, user_id ORDER BY m DESC LIMIT ?",
        (action, limit),
    ) as cur:
        return list(await cur.fetchall())


async def delete_action(chat_id: int, user_id: int, action: str) -> None:
    await _db.execute(
        "DELETE FROM actions WHERE chat_id = ? AND user_id = ? AND action = ?",
        (chat_id, user_id, action),
    )
    await _db.commit()


async def count_actions_since(action: str, since_ts: int) -> int:
    async with _db.execute(
        "SELECT COUNT(*) FROM actions WHERE action = ? AND ts >= ?", (action, since_ts)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def add_log(text: str) -> None:
    await _db.execute(
        "INSERT INTO action_log (ts, text) VALUES (?, ?)", (int(time.time()), text)
    )
    # Держим только последние 500 записей, чтобы БД не росла бесконечно
    await _db.execute(
        "DELETE FROM action_log WHERE id NOT IN "
        "(SELECT id FROM action_log ORDER BY id DESC LIMIT 500)"
    )
    await _db.commit()


async def get_recent_logs(limit: int = 15) -> list[tuple[int, str]]:
    async with _db.execute(
        "SELECT ts, text FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
    ) as cur:
        return list(await cur.fetchall())


# Фильтр истории по типу: сопоставление с ведущим эмодзи записи
_LOG_KIND_LIKE = {"ban": "🚫%", "mute": "🔇%"}


async def count_logs(kind: str = "all") -> int:
    like = _LOG_KIND_LIKE.get(kind)
    if like:
        query, args = "SELECT COUNT(*) FROM action_log WHERE text LIKE ?", (like,)
    else:
        query, args = "SELECT COUNT(*) FROM action_log", ()
    async with _db.execute(query, args) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def get_logs_page(limit: int, offset: int, kind: str = "all") -> list[tuple[int, str]]:
    like = _LOG_KIND_LIKE.get(kind)
    if like:
        query = "SELECT ts, text FROM action_log WHERE text LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?"
        args: tuple = (like, limit, offset)
    else:
        query = "SELECT ts, text FROM action_log ORDER BY id DESC LIMIT ? OFFSET ?"
        args = (limit, offset)
    async with _db.execute(query, args) as cur:
        return list(await cur.fetchall())


async def clear_logs() -> None:
    await _db.execute("DELETE FROM action_log")
    await _db.commit()


async def get_setting(key: str) -> str | None:
    async with _db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    await _db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await _db.commit()


async def close_db() -> None:
    if _db is not None:
        await _db.close()


async def get_warns(chat_id: int, user_id: int) -> int:
    async with _db.execute(
        "SELECT count FROM warns WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else 0


async def add_warn(chat_id: int, user_id: int) -> int:
    count = await get_warns(chat_id, user_id) + 1
    await _db.execute(
        "INSERT INTO warns (chat_id, user_id, count) VALUES (?, ?, ?) "
        "ON CONFLICT (chat_id, user_id) DO UPDATE SET count = excluded.count",
        (chat_id, user_id, count),
    )
    await _db.commit()
    return count


async def reset_warns(chat_id: int, user_id: int) -> None:
    await _db.execute("DELETE FROM warns WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    await _db.commit()


async def add_captcha(chat_id: int, user_id: int, message_id: int) -> None:
    await _db.execute(
        "INSERT OR REPLACE INTO captcha_pending (chat_id, user_id, message_id, join_ts) "
        "VALUES (?, ?, ?, ?)",
        (chat_id, user_id, message_id, int(time.time())),
    )
    await _db.commit()


async def pop_captcha(chat_id: int, user_id: int) -> tuple[int, int] | None:
    async with _db.execute(
        "SELECT message_id, join_ts FROM captcha_pending WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    await _db.execute(
        "DELETE FROM captcha_pending WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
    )
    await _db.commit()
    return row


async def get_expired_captchas(cutoff_ts: int) -> list[tuple[int, int, int]]:
    async with _db.execute(
        "SELECT chat_id, user_id, message_id FROM captcha_pending WHERE join_ts < ?",
        (cutoff_ts,),
    ) as cur:
        return list(await cur.fetchall())
