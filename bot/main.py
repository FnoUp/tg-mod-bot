import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot import database as db
from bot.config import config
from bot.handlers import admin, check, newmembers, panel, undo
from bot.middlewares.antidup import AntiDuplicateMiddleware
from bot.middlewares.antiflood import AntiFloodMiddleware
from bot.middlewares.antispam import AntiSpamMiddleware


async def _background_loop(bot: Bot) -> None:
    while True:
        try:
            await newmembers.check_expired_captchas(bot)
        except Exception:
            logging.exception("Ошибка в фоновой проверке капчи")
        await asyncio.sleep(15)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await db.init_db(config.db_path)

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.outer_middleware(AntiFloodMiddleware())
    dp.message.outer_middleware(AntiSpamMiddleware())
    dp.message.outer_middleware(AntiDuplicateMiddleware())

    dp.include_router(panel.router)
    dp.include_router(undo.router)
    dp.include_router(admin.router)
    dp.include_router(check.router)
    dp.include_router(newmembers.router)

    background_task = asyncio.create_task(_background_loop(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        background_task.cancel()
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
