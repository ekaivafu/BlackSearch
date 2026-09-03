import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.services.user_service import UserService
from bot.models.models import UserStatus

logger = logging.getLogger(__name__)

DAILY_BONUS_BANNER = (
    "🎁 <b>DAILY BONUS REWARD CREDITED!</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Welcome back, Operator! Your daily reconnaissance quota has been refreshed.\n\n"
    "💰 <b>Granted Today:</b> <b>+{amt} Search Credits</b>\n"
    "⏳ <b>Validity:</b> <b>Until 23:59 IST Tonight</b>\n\n"
    "<i>⚡ Tip: Daily bonus credits are used first before your permanent balance. Use them today!</i>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

class DailyBonusMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user or event.from_user.is_bot:
            return await handler(event, data)

        # Bypass admins to avoid spamming them
        if event.from_user.id in config.admin_ids:
            return await handler(event, data)

        # Only check in private chats
        if event.chat.type != "private":
            return await handler(event, data)

        session: AsyncSession = data.get("session")
        if session:
            user_service = UserService(session)
            user = await user_service.get_user_by_telegram_id(event.from_user.id)
            if user and user.status == UserStatus.APPROVED:
                granted, amt, _ = await user_service.check_and_apply_daily_bonus(user)
                if granted:
                    try:
                        await event.answer(
                            DAILY_BONUS_BANNER.format(amt=amt),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.debug(f"Failed to send daily bonus banner: {e}")

        return await handler(event, data)
