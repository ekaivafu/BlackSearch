import logging
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramAPIError
from bot.models.models import RequiredChannel

logger = logging.getLogger(__name__)

class ChannelService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_channels(self) -> List[RequiredChannel]:
        """Fetch all active required channels ordered by ID."""
        stmt = select(RequiredChannel).where(RequiredChannel.is_active == True).order_by(RequiredChannel.id.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_all_channels(self) -> List[RequiredChannel]:
        """Fetch all channels regardless of active status."""
        stmt = select(RequiredChannel).order_by(RequiredChannel.id.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_channel_by_id(self, record_id: int) -> Optional[RequiredChannel]:
        stmt = select(RequiredChannel).where(RequiredChannel.id == record_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_channel_by_chat_id(self, channel_id: int) -> Optional[RequiredChannel]:
        stmt = select(RequiredChannel).where(RequiredChannel.channel_id == channel_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def add_or_update_channel(
        self,
        channel_id: int,
        title: str,
        invite_link: str,
        username: Optional[str] = None
    ) -> RequiredChannel:
        """Add a channel or re-activate it if already present."""
        ch = await self.get_channel_by_chat_id(channel_id)
        if ch:
            ch.title = title
            ch.invite_link = invite_link
            ch.username = username
            ch.is_active = True
        else:
            ch = RequiredChannel(
                channel_id=channel_id,
                title=title,
                invite_link=invite_link,
                username=username,
                is_active=True
            )
            self.session.add(ch)
        await self.session.flush()
        return ch

    async def delete_channel(self, record_id: int) -> bool:
        """Permanently remove a required channel."""
        ch = await self.get_channel_by_id(record_id)
        if not ch:
            return False
        await self.session.delete(ch)
        await self.session.flush()
        return True

    async def check_user_membership(self, bot: Bot, user_id: int, channel: RequiredChannel) -> bool:
        """Check if a specific user is a member of the required channel."""
        try:
            member = await bot.get_chat_member(chat_id=channel.channel_id, user_id=user_id)
            status = getattr(member, "status", None)
            if status in ["creator", "administrator", "member"]:
                return True
            if status == "restricted":
                # User might be restricted in a group, but is still a member
                is_member = getattr(member, "is_member", True)
                return bool(is_member)
            return False
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            logger.warning(f"Could not verify user {user_id} in channel {channel.channel_id}: {e}")
            # If the bot was kicked or lost access, don't permanently lock innocent users out
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking user membership: {e}")
            return False

    async def get_missing_channels(self, bot: Bot, user_id: int) -> List[RequiredChannel]:
        """Return list of required channels the user has NOT joined yet."""
        channels = await self.get_active_channels()
        if not channels:
            return []

        missing = []
        for ch in channels:
            is_member = await self.check_user_membership(bot, user_id, ch)
            if not is_member:
                missing.append(ch)
        return missing

    @staticmethod
    async def verify_bot_admin_status(bot: Bot, chat_identifier: str | int) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Verify if the bot has access and is an Administrator in the given channel/group.
        Returns: (is_admin: bool, message: str, info_dict: dict)
        """
        try:
            chat = await bot.get_chat(chat_identifier)
        except TelegramBadRequest as e:
            return False, f"Could not find chat/channel with identifier '{chat_identifier}'. Error: {e.message}", {}
        except TelegramForbiddenError:
            return False, "The bot was blocked or does not have access to this channel/group. Please add the bot first!", {}
        except Exception as e:
            return False, f"Unexpected error reaching channel: {str(e)}", {}

        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat_id=chat.id, user_id=me.id)
            if member.status not in ["administrator", "creator"]:
                return False, (
                    f"⚠️ <b>Bot is NOT an Admin in '{chat.title}'!</b>\n\n"
                    "The bot is currently in the channel as a regular member.\n"
                    "Please promote this bot to <b>Administrator</b> with permission to check members, then try again."
                ), {}

            # Generate invite link if chat doesn't have a public username
            invite_link = None
            if chat.username:
                invite_link = f"https://t.me/{chat.username}"
            else:
                # Try to get primary invite link or export one
                try:
                    invite_link = chat.invite_link or await bot.export_chat_invite_link(chat.id)
                except Exception:
                    invite_link = chat.invite_link or f"https://t.me/c/{str(chat.id).replace('-100', '')}"

            info = {
                "channel_id": chat.id,
                "title": chat.title or "Untitled Channel",
                "username": f"@{chat.username}" if chat.username else None,
                "invite_link": invite_link,
            }
            return True, "Bot is verified as Administrator!", info

        except Exception as e:
            return False, f"Failed to check bot permissions: {str(e)}", {}
