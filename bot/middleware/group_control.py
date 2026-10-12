import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.services.group_service import GroupService

logger = logging.getLogger(__name__)

class GroupControlMiddleware(BaseMiddleware):
    """
    Middleware that controls bot behavior in Telegram groups and supergroups.
    - Automatically discovers and tracks new groups.
    - If a group is turned OFF by admin, the bot 'ghosts' in that group:
      it drops all updates completely, remaining 100% silent and ignoring messages.
    - Private chats are always permitted and never ghosted.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        chat = None
        user = getattr(event, "from_user", None)

        if isinstance(event, Message) or hasattr(event, "chat"):
            chat = getattr(event, "chat", None)
        elif isinstance(event, CallbackQuery) or hasattr(event, "message"):
            msg = getattr(event, "message", None)
            chat = getattr(msg, "chat", None) if msg else None

        # If not a chat or is a private chat, let it pass through
        if not chat or chat.type == "private":
            return await handler(event, data)

        # Chat is a group or supergroup
        if chat.type in ("group", "supergroup"):
            # 1. Fast in-memory cache check (<0.01ms)
            cached_status = GroupService.get_cached_group_status(chat.id)
            if cached_status is False:
                # Group is OFF -> Ghost mode! Stay completely silent
                logger.debug(f"[GhostMode] Ignoring update in turned-off group {chat.id} ({chat.title})")
                return None

            if cached_status is True:
                # Group is explicitly active
                return await handler(event, data)

            # 2. Cache miss: Lookup in DB or auto-register the group
            session: AsyncSession = data.get("session")
            if session:
                try:
                    group_service = GroupService(session)
                    group = await group_service.get_or_create_group(
                        chat_id=chat.id,
                        title=chat.title or f"Group {chat.id}",
                        username=chat.username,
                        added_by=user.id if user else None
                    )
                    if not group.is_active:
                        logger.info(f"[GhostMode] Group {chat.id} ({group.title}) is OFF. Dropping update.")
                        return None
                except Exception as e:
                    logger.error(f"Error in GroupControlMiddleware for chat {chat.id}: {e}")
                    # In case of DB error, do not crash; default to allowing
                    return await handler(event, data)

        return await handler(event, data)
