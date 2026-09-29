import logging
import re
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from bot.models.models import Seller

logger = logging.getLogger(__name__)

# Global cache flag: None = unknown, True = has sellers, False = no sellers
_GLOBAL_HAS_SELLERS: Optional[bool] = None

class SellerService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @classmethod
    def get_cached_has_sellers(cls) -> Optional[bool]:
        """Returns the current in-memory cache of whether sellers exist."""
        return _GLOBAL_HAS_SELLERS

    @classmethod
    def set_cached_has_sellers(cls, status: bool) -> None:
        """Sets the in-memory cache flag."""
        global _GLOBAL_HAS_SELLERS
        _GLOBAL_HAS_SELLERS = status

    async def has_sellers(self) -> bool:
        """Check if at least one active seller is configured in the system."""
        stmt = select(func.count(Seller.id)).where(Seller.is_active == True)
        res = await self.session.execute(stmt)
        count = res.scalar() or 0
        has = count > 0
        self.set_cached_has_sellers(has)
        return has

    async def get_active_sellers(self) -> List[Seller]:
        """Fetch all active sellers."""
        stmt = select(Seller).where(Seller.is_active == True).order_by(Seller.id.asc())
        res = await self.session.execute(stmt)
        sellers = list(res.scalars().all())
        self.set_cached_has_sellers(len(sellers) > 0)
        return sellers

    async def get_seller_by_id(self, seller_id: int) -> Optional[Seller]:
        """Fetch a seller by database ID."""
        stmt = select(Seller).where(Seller.id == seller_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_seller_by_username(self, username: str) -> Optional[Seller]:
        """Fetch a seller by username (case-insensitive, ignoring leading @)."""
        clean = username.strip().lstrip("@").lower()
        stmt = select(Seller).where(func.lower(Seller.username) == clean)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def add_seller(
        self,
        username_or_id: str,
        telegram_id: Optional[int] = None,
        name: Optional[str] = None,
        created_by: Optional[int] = None
    ) -> Tuple[bool, str, Optional[Seller]]:
        """
        Add a new seller or re-activate an existing one.
        Accepts Telegram username (with/without @) or numeric ID.
        """
        raw = str(username_or_id).strip()
        if not raw:
            return False, "Seller username or ID cannot be empty.", None

        # Clean username
        clean_username = raw.lstrip("@").strip()
        
        # Check if input was purely numeric (e.g. Telegram ID)
        if clean_username.isdigit() and telegram_id is None:
            telegram_id = int(clean_username)

        # Check existing seller
        existing = await self.get_seller_by_username(clean_username)
        if existing:
            if existing.is_active:
                return False, f"Seller <b>@{existing.username}</b> is already registered and active.", existing
            else:
                existing.is_active = True
                if telegram_id:
                    existing.telegram_id = telegram_id
                if name:
                    existing.name = name
                await self.session.flush()
                self.set_cached_has_sellers(True)
                return True, f"Seller <b>@{existing.username}</b> has been re-activated!", existing

        new_seller = Seller(
            username=clean_username,
            telegram_id=telegram_id,
            name=name or clean_username,
            is_active=True,
            created_by=created_by
        )
        self.session.add(new_seller)
        await self.session.flush()
        self.set_cached_has_sellers(True)
        return True, f"Seller <b>@{clean_username}</b> successfully added!", new_seller

    async def delete_seller(self, seller_id: int) -> bool:
        """Permanently delete a seller by ID."""
        stmt = delete(Seller).where(Seller.id == seller_id)
        res = await self.session.execute(stmt)
        await self.session.flush()
        # Refresh cache
        await self.has_sellers()
        return res.rowcount > 0

    async def format_sellers_summary(self) -> str:
        """
        Returns a human-readable contact text for users requesting recharge:
        e.g. "@seller1" or "@seller1 or @seller2"
        """
        sellers = await self.get_active_sellers()
        if not sellers:
            return "our administrative support"
        
        usernames = [f"@{s.username}" for s in sellers]
        if len(usernames) == 1:
            return usernames[0]
        elif len(usernames) == 2:
            return f"{usernames[0]} or {usernames[1]}"
        else:
            return ", ".join(usernames[:-1]) + f", or {usernames[-1]}"

    async def format_recharge_notice(self) -> str:
        """
        Builds the standard purchase instruction paragraph for users.
        """
        sellers = await self.get_active_sellers()
        if not sellers:
            return "👉 <b>Please contact admin support to complete your payment.</b>"
        
        if len(sellers) == 1:
            uname = sellers[0].username
            return f"👉 <b>Please message @{uname} to complete your payment.</b>"
        
        lines = ["👉 <b>Please message any of our official sellers to complete your payment:</b>"]
        for s in sellers:
            lines.append(f"• 👤 <b>@{s.username}</b> (https://t.me/{s.username})")
        return "\n".join(lines)
