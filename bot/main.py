import asyncio
import logging
from dotenv import load_dotenv
import os

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import config
from bot.database.session import async_session, init_db
from bot.middleware.db import DbSessionMiddleware
from bot.middleware.auth import ThrottlingMiddleware
from bot.handlers import user, admin
from bot.services import duckdb_service

from aiohttp import web

logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Dummy web server started on port {port} for Render")

async def main():
    # Start web server so Render doesn't kill the bot
    await start_web_server()

    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Middlewares
    dp.update.middleware(DbSessionMiddleware(async_session))
    dp.message.middleware(ThrottlingMiddleware(limit_seconds=1.0))
    
    # Routers
    dp.include_router(user.router)
    dp.include_router(admin.router)
    
    # Database auto-migration and plan seeding
    await init_db()

    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
