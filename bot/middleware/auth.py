from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
import time

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit_seconds: float = 1.0):
        self.limit_seconds = limit_seconds
        self.caches: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            now = time.time()
            if user_id in self.caches:
                if now - self.caches[user_id] < self.limit_seconds:
                    return None  # Drop duplicate rapid messages
            self.caches[user_id] = now

            # Clean cache if it gets large to prevent memory leak
            if len(self.caches) > 1000:
                cutoff = now - (self.limit_seconds * 10)
                self.caches = {uid: t for uid, t in self.caches.items() if t > cutoff}
            
        return await handler(event, data)
