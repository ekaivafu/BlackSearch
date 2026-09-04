import logging
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_
from bot.models.models import Blacklist

logger = logging.getLogger(__name__)

class BlacklistService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def normalize_value(target_type: str, value: str) -> str:
        """Normalize target value based on type for consistent matching."""
        if not value:
            return ""
        val = str(value).strip()
        t = target_type.lower()
        
        if t == "phone":
            digits = "".join(ch for ch in val if ch.isdigit())
            # If 12 digits starting with 91 (standard Indian mobile with country code), normalize to 10 digits
            if len(digits) == 12 and digits.startswith("91"):
                return digits[2:]
            return digits
        elif t == "email":
            return val.lower()
        elif t == "username":
            return val.lstrip("@").strip().lower()
        return val.strip().lower()

    async def is_blacklisted(self, target_type: str, raw_query: str) -> bool:
        """
        Check if a given query is blacklisted.
        Handles normalization and phone number variations.
        """
        if not raw_query:
            return False
            
        t = target_type.lower()
        norm = self.normalize_value(t, raw_query)
        if not norm:
            return False

        if t == "phone":
            # For phone, match against 10-digit norm or with 91 prefix
            conditions = [Blacklist.value == norm]
            if len(norm) == 10:
                conditions.append(Blacklist.value == f"91{norm}")
            elif len(norm) == 12 and norm.startswith("91"):
                conditions.append(Blacklist.value == norm[2:])
            
            stmt = select(Blacklist).where(
                Blacklist.target_type == "phone",
                or_(*conditions)
            ).limit(1)
        else:
            stmt = select(Blacklist).where(
                Blacklist.target_type == t,
                Blacklist.value == norm
            ).limit(1)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def add_to_blacklist(
        self,
        target_type: str,
        value: str,
        created_by: Optional[int] = None,
        note: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Blacklist]]:
        """
        Add an item to the blocklist.
        Returns (success, message, record).
        """
        t = target_type.lower()
        norm = self.normalize_value(t, value)
        if not norm:
            return False, "Invalid or empty value provided.", None

        # Check if already blacklisted
        already_blocked = await self.is_blacklisted(t, norm)
        if already_blocked:
            return False, f"This {t} (<code>{norm}</code>) is already in the blocklist.", None

        record = Blacklist(
            target_type=t,
            value=norm,
            note=note,
            created_by=created_by
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        logger.info(f"Blacklisted {t}: {norm} by admin {created_by}")
        return True, f"Successfully blacklisted {t}: <code>{norm}</code>", record

    async def remove_from_blacklist(self, blacklist_id: int) -> bool:
        """Remove an item from the blocklist by ID."""
        stmt = delete(Blacklist).where(Blacklist.id == blacklist_id)
        res = await self.session.execute(stmt)
        await self.session.commit()
        return (res.rowcount or 0) > 0

    async def get_by_id(self, blacklist_id: int) -> Optional[Blacklist]:
        """Fetch a single blocklist record by ID."""
        stmt = select(Blacklist).where(Blacklist.id == blacklist_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_all(self, target_type: Optional[str] = None) -> List[Blacklist]:
        """Fetch all blocklist records, optionally filtered by type."""
        stmt = select(Blacklist)
        if target_type:
            stmt = stmt.where(Blacklist.target_type == target_type.lower())
        stmt = stmt.order_by(Blacklist.created_at.desc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_counts(self) -> dict:
        """Get counts of blocked items per type."""
        all_items = await self.get_all()
        counts = {"phone": 0, "email": 0, "username": 0, "total": len(all_items)}
        for item in all_items:
            if item.target_type in counts:
                counts[item.target_type] += 1
        return counts
