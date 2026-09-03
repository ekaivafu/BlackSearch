import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.services.channel_service import ChannelService
from bot.keyboards.inline import get_force_sub_keyboard

logger = logging.getLogger(__name__)

RESTRICTED_TEXT = (
    "🔒 <b>Access Restricted — Verification Required</b>\n\n"
    "Welcome to <b>BlackSearch OSINT Engine</b>! ⚡\n\n"
    "To access searches, personal lookup tools, and free credits, you must first join our official update channel(s) below.\n\n"
    "👉 <b>Step 1:</b> Click the channel button(s) below and join.\n"
    "👉 <b>Step 2:</b> Return here and click <b>🔄 Check Again / Verify</b> to unlock instant access!\n\n"
    "<i>Staying subscribed ensures you receive database updates and active features.</i>"
)

class ForceSubMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        bot: Bot = data.get("bot")
        session: AsyncSession = data.get("session")

        # Determine user ID and chat type
        user = None
        if isinstance(event, Message):
            user = event.from_user
            # Allow private chats only or handle group gracefully
            if event.chat.type != "private":
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            # Always allow the verification button handler through
            if event.data == "verify_sub":
                return await handler(event, data)

        if not user or not bot or not session:
            return await handler(event, data)

        # 👑 Admins bypass force-subscription completely
        if user.id in config.admin_ids:
            return await handler(event, data)

        # Check if any required channels exist
        try:
            cs = ChannelService(session)
            missing = await cs.get_missing_channels(bot, user.id)
            if missing:
                keyboard = get_force_sub_keyboard(missing)
                if isinstance(event, Message):
                    await event.answer(RESTRICTED_TEXT, reply_markup=keyboard, parse_mode="HTML")
                    return None
                elif isinstance(event, CallbackQuery):
                    await event.answer("⚠️ You must join our required channel(s) to use this bot!", show_alert=True)
                    try:
                        await event.message.answer(RESTRICTED_TEXT, reply_markup=keyboard, parse_mode="HTML")
                    except Exception:
                        pass
                    return None
        except Exception as e:
            logger.error(f"Error in ForceSubMiddleware: {e}")
            # In case of Telegram API glitches, don't lock innocent users out
            return await handler(event, data)

        return await handler(event, data)
