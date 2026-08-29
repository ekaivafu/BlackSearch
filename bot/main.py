import asyncio
import logging
from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config
from bot.database.session import async_session
from bot.middleware.db import DbSessionMiddleware
from bot.middleware.auth import ThrottlingMiddleware
from bot.handlers import user, admin
from bot.services import duckdb_service

logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Middlewares
    dp.update.middleware(DbSessionMiddleware(async_session))
    dp.message.middleware(ThrottlingMiddleware(limit_seconds=1.0))
    
    # Routers
    dp.include_router(user.router)
    dp.include_router(admin.router)
    
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
