import logging
from typing import Optional, List, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, update
from bot.models.models import ManagedGroup

logger = logging.getLogger(__name__)

# Fast in-memory cache: chat_id (int) -> is_active (bool)
_GROUP_STATUS_CACHE: Dict[int, bool] = {}

class GroupService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @classmethod
    def get_cached_group_status(cls, chat_id: int) -> Optional[bool]:
        """Returns cached active status for chat_id, or None if not cached."""
        return _GROUP_STATUS_CACHE.get(chat_id)

    @classmethod
    def set_cached_group_status(cls, chat_id: int, is_active: bool) -> None:
        """Sets the cached status for chat_id."""
        _GROUP_STATUS_CACHE[chat_id] = is_active

    @classmethod
    def remove_from_cache(cls, chat_id: int) -> None:
        """Removes a chat_id from cache."""
        _GROUP_STATUS_CACHE.pop(chat_id, None)

    @classmethod
    def clear_cache(cls) -> None:
        """Clears all cached group statuses."""
        _GROUP_STATUS_CACHE.clear()

    async def get_all_groups(self) -> List[ManagedGroup]:
        """Fetch all managed groups sorted by ID."""
        stmt = select(ManagedGroup).order_by(ManagedGroup.id.asc())
        res = await self.session.execute(stmt)
        groups = list(res.scalars().all())
        # Update cache for all loaded groups
        for g in groups:
            self.set_cached_group_status(g.chat_id, g.is_active)
        return groups

    async def get_group_by_id(self, group_id: int) -> Optional[ManagedGroup]:
        """Fetch a group by database primary key ID."""
        stmt = select(ManagedGroup).where(ManagedGroup.id == group_id)
        res = await self.session.execute(stmt)
        group = res.scalar_one_or_none()
        if group:
            self.set_cached_group_status(group.chat_id, group.is_active)
        return group

    async def get_group_by_chat_id(self, chat_id: int) -> Optional[ManagedGroup]:
        """Fetch a group by its Telegram chat_id."""
        stmt = select(ManagedGroup).where(ManagedGroup.chat_id == chat_id)
        res = await self.session.execute(stmt)
        group = res.scalar_one_or_none()
        if group:
            self.set_cached_group_status(group.chat_id, group.is_active)
        return group

    async def get_or_create_group(
        self,
        chat_id: int,
        title: str,
        username: Optional[str] = None,
        added_by: Optional[int] = None,
        member_count: Optional[int] = None
    ) -> ManagedGroup:
        """
        Auto-discovers and registers a group, or updates its metadata if it already exists.
        Preserves existing is_active setting.
        """
        group = await self.get_group_by_chat_id(chat_id)
        if group:
            updated = False
            clean_title = (title or "").strip() or group.title
            clean_uname = (username or "").lstrip("@").strip() if username else group.username
            if group.title != clean_title:
                group.title = clean_title
                updated = True
            if group.username != clean_uname:
                group.username = clean_uname
                updated = True
            if member_count is not None and group.member_count != member_count:
                group.member_count = member_count
                updated = True
            if updated:
                await self.session.flush()
            self.set_cached_group_status(group.chat_id, group.is_active)
            return group

        # Create new group with is_active=True by default
        clean_title = (title or "").strip() or f"Group {chat_id}"
        clean_uname = (username or "").lstrip("@").strip() if username else None
        new_group = ManagedGroup(
            chat_id=chat_id,
            title=clean_title,
            username=clean_uname,
            is_active=True,
            member_count=member_count,
            added_by=added_by
        )
        self.session.add(new_group)
        await self.session.flush()
        self.set_cached_group_status(new_group.chat_id, True)
        logger.info(f"Registered new managed group: '{clean_title}' (chat_id={chat_id})")
        return new_group

    async def add_group_manual(
        self,
        chat_id: int,
        title: str,
        username: Optional[str] = None,
        added_by: Optional[int] = None
    ) -> Tuple[bool, str, Optional[ManagedGroup]]:
        """Manually add or reactivate a group by admin."""
        existing = await self.get_group_by_chat_id(chat_id)
        if existing:
            existing.title = title or existing.title
            if username:
                existing.username = username.lstrip("@").strip()
            existing.is_active = True
            await self.session.flush()
            self.set_cached_group_status(existing.chat_id, True)
            return True, f"Group <b>{existing.title}</b> is already registered and was re-activated (ON).", existing

        new_group = ManagedGroup(
            chat_id=chat_id,
            title=title or f"Group {chat_id}",
            username=username.lstrip("@").strip() if username else None,
            is_active=True,
            added_by=added_by
        )
        self.session.add(new_group)
        await self.session.flush()
        self.set_cached_group_status(new_group.chat_id, True)
        return True, f"Group <b>{new_group.title}</b> registered successfully!", new_group

    async def toggle_group_status(self, group_id: int) -> Tuple[bool, Optional[ManagedGroup]]:
        """Toggles the active (ON/OFF) status of a group."""
        group = await self.get_group_by_id(group_id)
        if not group:
            return False, None
        group.is_active = not group.is_active
        await self.session.flush()
        self.set_cached_group_status(group.chat_id, group.is_active)
        logger.info(f"Group '{group.title}' (ID {group.id}, chat_id {group.chat_id}) toggled to is_active={group.is_active}")
        return True, group

    async def set_group_status(self, group_id: int, is_active: bool) -> Tuple[bool, Optional[ManagedGroup]]:
        """Sets the active status of a specific group."""
        group = await self.get_group_by_id(group_id)
        if not group:
            return False, None
        group.is_active = is_active
        await self.session.flush()
        self.set_cached_group_status(group.chat_id, is_active)
        return True, group

    async def set_all_groups_status(self, is_active: bool) -> int:
        """Batch toggle all groups to active (ON) or ghosted (OFF)."""
        stmt = update(ManagedGroup).values(is_active=is_active)
        res = await self.session.execute(stmt)
        await self.session.flush()
        # Update cache for all known groups
        all_groups = await self.get_all_groups()
        for g in all_groups:
            self.set_cached_group_status(g.chat_id, is_active)
        return res.rowcount or len(all_groups)

    async def delete_group(self, group_id: int) -> Tuple[bool, str]:
        """Deletes a group from database and cache."""
        group = await self.get_group_by_id(group_id)
        if not group:
            return False, ""
        title = group.title
        chat_id = group.chat_id
        await self.session.execute(delete(ManagedGroup).where(ManagedGroup.id == group_id))
        await self.session.flush()
        self.remove_from_cache(chat_id)
        logger.info(f"Deleted managed group '{title}' (chat_id={chat_id})")
        return True, title

    async def get_stats(self) -> Dict[str, int]:
        """Get counts of total, active, and ghosted groups."""
        stmt = select(
            func.count(ManagedGroup.id),
            func.count(ManagedGroup.id).filter(ManagedGroup.is_active == True)
        )
        res = await self.session.execute(stmt)
        row = res.one_or_none()
        total = row[0] if row else 0
        active = row[1] if row else 0
        ghosted = total - active
        return {
            "total": total,
            "active": active,
            "ghosted": ghosted
        }
