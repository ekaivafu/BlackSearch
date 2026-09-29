import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.services.seller_service import SellerService

logger = logging.getLogger(__name__)

class SellerCheckMiddleware(BaseMiddleware):
    """
    Ensures that when no seller username/ID has been configured in the system,
    the bot strictly refrains from replying to regular users until an admin sets one.
    Admins are always exempt from this restriction so they can configure the bot.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if not user:
            return await handler(event, data)

        # 👑 Admins are always allowed so they can access /admin and configure sellers
        if user.id in config.admin_ids:
            return await handler(event, data)

        # Check cached seller availability
        has_sellers = SellerService.get_cached_has_sellers()
        if has_sellers is None:
            session: AsyncSession = data.get("session")
            if session:
                try:
                    seller_service = SellerService(session)
                    has_sellers = await seller_service.has_sellers()
                except Exception as e:
                    logger.error(f"Error checking sellers in SellerCheckMiddleware: {e}")
                    has_sellers = True  # Avoid lockout on transient DB errors

        # If no seller is configured, strictly ignore / do not reply to regular users
        if not has_sellers:
            logger.info(f"Ignoring message from non-admin user {user.id} because no seller is configured.")
            return None

        return await handler(event, data)
