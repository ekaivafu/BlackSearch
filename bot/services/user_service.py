import logging
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from aiogram import Bot
from bot.models.models import User, UserStatus, CreditTransaction, TransactionType, RechargeRequest, RechargeStatus, SearchLog
import datetime

logger = logging.getLogger(__name__)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)

def get_today_ist() -> datetime.date:
    return get_now_ist().date()

def get_end_of_today_ist() -> datetime.datetime:
    now = get_now_ist()
    return datetime.datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=IST)

class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_user_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def check_and_apply_daily_bonus(self, user: User) -> Tuple[bool, int, Optional[datetime.datetime]]:
        """
        Checks and applies the daily free bonus for the user's 1st interaction of the day.
        Expires any old bonus credits past 23:59 IST.
        Returns: (granted: bool, amount: int, expire_at: Optional[datetime])
        """
        now = get_now_ist()
        # 1. Expire outdated bonus credits if past expiration
        if user.bonus_credits_expire_at and now > user.bonus_credits_expire_at:
            user.bonus_credits = 0
            user.bonus_credits_expire_at = None

        today = get_today_ist()
        if user.last_daily_bonus_date != today:
            from bot.services.plan_service import PlanService
            ps = PlanService(self.session)
            daily_bonus = await ps.get_daily_bonus_credits()
            if daily_bonus > 0:
                user.bonus_credits = daily_bonus
                user.bonus_credits_expire_at = get_end_of_today_ist()
                user.last_daily_bonus_date = today

                tx = CreditTransaction(
                    user_id=user.id,
                    amount=daily_bonus,
                    transaction_type=TransactionType.DAILY_BONUS,
                    balance_after=user.credits + user.bonus_credits,
                    created_by=user.telegram_user_id
                )
                self.session.add(tx)
                await self.session.flush()
                return True, daily_bonus, user.bonus_credits_expire_at

        return False, 0, None

    @staticmethod
    def get_effective_credits(user: User) -> int:
        now = get_now_ist()
        if user.bonus_credits_expire_at and now > user.bonus_credits_expire_at:
            user.bonus_credits = 0
            user.bonus_credits_expire_at = None
        return user.credits + (user.bonus_credits or 0)

    async def create_user(
        self,
        telegram_id: int,
        username: str = None,
        first_name: str = None,
        last_name: str = None,
        referred_by: Optional[int] = None
    ) -> User:
        from bot.services.plan_service import PlanService
        ps = PlanService(self.session)
        init_credits = await ps.get_initial_credits()
        daily_bonus = await ps.get_daily_bonus_credits()

        # Validate referrer (cannot refer self, referrer must exist)
        valid_referrer = None
        if referred_by and referred_by != telegram_id:
            referrer_obj = await self.get_user_by_telegram_id(referred_by)
            if referrer_obj:
                valid_referrer = referred_by

        user = User(
            telegram_user_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            status=UserStatus.APPROVED,
            credits=init_credits,
            bonus_credits=daily_bonus,
            bonus_credits_expire_at=get_end_of_today_ist() if daily_bonus > 0 else None,
            last_daily_bonus_date=get_today_ist() if daily_bonus > 0 else None,
            referred_by=valid_referrer,
            referral_reward_claimed=False,
            referral_count=0,
            referral_credits_earned=0
        )
        self.session.add(user)
        await self.session.flush()

        if init_credits > 0:
            tx = CreditTransaction(
                user_id=user.id,
                amount=init_credits,
                transaction_type=TransactionType.INITIAL_GRANT,
                balance_after=init_credits,
                created_by=telegram_id
            )
            self.session.add(tx)
            await self.session.flush()

        if daily_bonus > 0:
            tx_bonus = CreditTransaction(
                user_id=user.id,
                amount=daily_bonus,
                transaction_type=TransactionType.DAILY_BONUS,
                balance_after=init_credits + daily_bonus,
                created_by=telegram_id
            )
            self.session.add(tx_bonus)
            await self.session.flush()

        return user

    async def process_referral_reward(self, bot: Bot, new_user: User) -> Tuple[bool, str]:
        """
        Validates and awards referral bonus to the inviter when a referred user joins
        channels and completes verification.
        Security rules:
        1. User must have a referrer.
        2. Reward must not have been claimed yet.
        3. User must have passed channel verification.
        4. User MUST have a Telegram username (anti-bot protection).
        5. Cannot refer oneself.
        """
        import html
        if not new_user.referred_by:
            return False, "No referrer"

        if new_user.referral_reward_claimed:
            return False, "Already claimed"

        if not new_user.is_channel_verified:
            return False, "Channel verification not completed"

        if not new_user.username or not new_user.username.strip():
            logger.info(f"Referral skipped for user {new_user.telegram_user_id}: No username")
            return False, "User has no Telegram username"

        if new_user.referred_by == new_user.telegram_user_id:
            return False, "Self referral"

        referrer = await self.get_user_by_telegram_id(new_user.referred_by)
        if not referrer:
            return False, "Referrer account not found"

        # Mark claimed before flush to prevent race conditions
        new_user.referral_reward_claimed = True

        from bot.services.plan_service import PlanService
        ps = PlanService(self.session)
        reward = await ps.get_referral_reward_credits()

        if reward > 0:
            referrer.credits += reward
            referrer.referral_count = (referrer.referral_count or 0) + 1
            referrer.referral_credits_earned = (referrer.referral_credits_earned or 0) + reward

            tx = CreditTransaction(
                user_id=referrer.id,
                amount=reward,
                transaction_type=TransactionType.REFERRAL,
                balance_after=referrer.credits + (referrer.bonus_credits or 0),
                created_by=new_user.telegram_user_id
            )
            self.session.add(tx)
            await self.session.flush()

            friend_name = html.escape(f"{new_user.first_name or ''} {new_user.last_name or ''}".strip() or "A friend")
            friend_user = f"@{html.escape(new_user.username)}"
            referrer_msg = (
                "🎉 <b>REFERRAL REWARD CREDITED!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Your invited friend <b>{friend_name}</b> ({friend_user}) joined our channel and completed verification!\n\n"
                f"💰 <b>Reward Received:</b> <b>+{reward} Permanent Credits</b>\n"
                f"👥 <b>Total Verified Referrals:</b> <code>{referrer.referral_count}</code>\n"
                f"🪙 <b>Your Search Balance:</b> <code>{referrer.credits} permanent credits</code>\n\n"
                "<i>Keep sharing your link to earn more free searches!</i>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            try:
                await bot.send_message(
                    chat_id=referrer.telegram_user_id,
                    text=referrer_msg,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.debug(f"Could not notify referrer {referrer.telegram_user_id}: {e}")

            return True, f"Awarded {reward} credits"

        return False, "Reward is 0"

    async def get_pending_users(self) -> List[User]:
        stmt = select(User).where(User.status == UserStatus.PENDING).order_by(User.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def approve_user(self, telegram_id: int, admin_id: int, initial_credits: int = None) -> Optional[User]:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user or user.status != UserStatus.PENDING:
            return None

        if initial_credits is None:
            from bot.services.plan_service import PlanService
            ps = PlanService(self.session)
            initial_credits = await ps.get_initial_credits()

        user.status = UserStatus.APPROVED
        user.credits = initial_credits
        user.approved_at = datetime.datetime.now(datetime.timezone.utc)
        user.approved_by = admin_id

        tx = CreditTransaction(
            user_id=user.id,
            amount=initial_credits,
            transaction_type=TransactionType.INITIAL_GRANT,
            balance_after=initial_credits + (user.bonus_credits or 0),
            created_by=admin_id
        )
        self.session.add(tx)
        await self.session.flush()
        return user

    async def reject_user(self, telegram_id: int, admin_id: int) -> Optional[User]:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user or user.status != UserStatus.PENDING:
            return None
        
        user.status = UserStatus.REJECTED
        await self.session.flush()
        return user

    async def deduct_credit(self, telegram_id: int, amount: int = 1) -> bool:
        """Deducts credit safely. Must be called in a transaction."""
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user or user.status != UserStatus.APPROVED:
            return False

        # If user has an active unlimited subscription, allow the search for free
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if user.subscription_end and user.subscription_end > now_utc:
            return True

        now = get_now_ist()
        if user.bonus_credits_expire_at and now > user.bonus_credits_expire_at:
            user.bonus_credits = 0
            user.bonus_credits_expire_at = None

        total_available = user.credits + (user.bonus_credits or 0)
        if total_available < amount:
            return False

        # Deduct from expiring daily bonus credits first!
        remaining = amount
        if user.bonus_credits > 0:
            from_bonus = min(user.bonus_credits, remaining)
            user.bonus_credits -= from_bonus
            remaining -= from_bonus

        if remaining > 0:
            user.credits -= remaining

        tx = CreditTransaction(
            user_id=user.id,
            amount=-amount,
            transaction_type=TransactionType.SEARCH,
            balance_after=user.credits + (user.bonus_credits or 0),
            created_by=telegram_id
        )
        self.session.add(tx)
        await self.session.flush()
        return True

    async def request_recharge(self, telegram_id: int, amount: int, plan_id: Optional[int] = None) -> Optional[RechargeRequest]:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return None
        
        # Check if there is already a pending request
        stmt = select(RechargeRequest).where(
            RechargeRequest.user_id == user.id,
            RechargeRequest.status == RechargeStatus.PENDING
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none():
            return None # Already pending

        req = RechargeRequest(
            user_id=user.id,
            requested_credits=amount,
            plan_id=plan_id,
            status=RechargeStatus.PENDING
        )
        self.session.add(req)
        await self.session.flush()
        return req
    
    async def get_recharge_request(self, request_id: int) -> Optional[RechargeRequest]:
        stmt = select(RechargeRequest).where(RechargeRequest.id == request_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def approve_recharge(self, request_id: int, admin_id: int, approved_amount: Optional[int] = None) -> Optional[RechargeRequest]:
        req = await self.get_recharge_request(request_id)
        if not req or req.status != RechargeStatus.PENDING:
            return None

        if approved_amount is None:
            approved_amount = req.requested_credits

        req.status = RechargeStatus.APPROVED
        req.requested_credits = approved_amount
        req.processed_at = datetime.datetime.now(datetime.timezone.utc)
        req.processed_by = admin_id

        # Get user
        stmt = select(User).where(User.id == req.user_id)
        user = (await self.session.execute(stmt)).scalar_one_or_none()
        if user:
            # Check if associated with a dynamic Plan
            plan = None
            if req.plan_id:
                from bot.services.plan_service import PlanService
                from bot.models.models import PlanType
                ps = PlanService(self.session)
                plan = await ps.get_plan_by_id(req.plan_id)

            if plan:
                from bot.models.models import PlanType
                if plan.plan_type == PlanType.DAYS:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    start_time = max(now, user.subscription_end) if user.subscription_end else now
                    user.subscription_end = start_time + datetime.timedelta(days=plan.days)
                    tx_amount = 0
                else:
                    user.credits += plan.credits
                    tx_amount = plan.credits
            else:
                # Special legacy packages: -1 = 1 Day Unlimited, -7 = 7 Days Unlimited
                if approved_amount == -1:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    start_time = max(now, user.subscription_end) if user.subscription_end else now
                    user.subscription_end = start_time + datetime.timedelta(days=1)
                    tx_amount = 0 # No credit change
                elif approved_amount == -7:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    start_time = max(now, user.subscription_end) if user.subscription_end else now
                    user.subscription_end = start_time + datetime.timedelta(days=7)
                    tx_amount = 0 # No credit change
                else:
                    user.credits += approved_amount
                    tx_amount = approved_amount

            tx = CreditTransaction(
                user_id=user.id,
                amount=tx_amount,
                transaction_type=TransactionType.RECHARGE,
                reference_id=str(req.id),
                balance_after=user.credits,
                created_by=admin_id
            )
            self.session.add(tx)

        await self.session.flush()
        return req

    async def reject_recharge(self, request_id: int, admin_id: int) -> Optional[RechargeRequest]:
        req = await self.get_recharge_request(request_id)
        if not req or req.status != RechargeStatus.PENDING:
            return None

        req.status = RechargeStatus.REJECTED
        req.processed_at = datetime.datetime.now(datetime.timezone.utc)
        req.processed_by = admin_id
        await self.session.flush()
        return req

    async def ban_user(self, telegram_id: int) -> bool:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user:
            return False
        user.status = UserStatus.DISABLED
        await self.session.flush()
        return True

    async def unban_user(self, telegram_id: int) -> bool:
        user = await self.get_user_by_telegram_id(telegram_id)
        if not user or user.status != UserStatus.DISABLED:
            return False
        user.status = UserStatus.APPROVED
        await self.session.flush()
        return True

    async def delete_user(self, target_id: int) -> bool:
        """Completely delete a user and their associated records from the database."""
        # Find user by telegram_user_id first (BigInteger)
        user = await self.get_user_by_telegram_id(target_id)
        if not user and abs(target_id) <= 2147483647:
            # Fallback to internal database ID only if within 32-bit integer range
            stmt = select(User).where(User.id == target_id)
            res = await self.session.execute(stmt)
            user = res.scalar_one_or_none()

        if not user:
            return False

        # Clean up related child records first to ensure zero foreign key issues
        await self.session.execute(delete(CreditTransaction).where(CreditTransaction.user_id == user.id))
        await self.session.execute(delete(RechargeRequest).where(RechargeRequest.user_id == user.id))
        await self.session.execute(delete(SearchLog).where(SearchLog.user_id == user.id))

        await self.session.delete(user)
        await self.session.flush()
        return True

    async def get_all_approved_users(self) -> List[User]:
        stmt = select(User).where(User.status == UserStatus.APPROVED)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_users(self) -> List[User]:
        """Return every user (all statuses) ordered by join date. Used for admin reports."""
        stmt = select(User).order_by(User.created_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_stats(self) -> dict:
        from sqlalchemy import func
        stats = {}
        
        # Total Users
        stmt = select(func.count(User.id))
        stats["total_users"] = (await self.session.execute(stmt)).scalar() or 0
        
        # Approved Users
        stmt = select(func.count(User.id)).where(User.status == UserStatus.APPROVED)
        stats["approved_users"] = (await self.session.execute(stmt)).scalar() or 0
        
        # Banned Users
        stmt = select(func.count(User.id)).where(User.status == UserStatus.DISABLED)
        stats["banned_users"] = (await self.session.execute(stmt)).scalar() or 0
        
        # Total Searches
        stmt = select(func.sum(User.total_searches))
        stats["total_searches"] = (await self.session.execute(stmt)).scalar() or 0
        
        # Total Credits Floating
        stmt = select(func.sum(User.credits)).where(User.status == UserStatus.APPROVED)
        stats["total_credits"] = (await self.session.execute(stmt)).scalar() or 0
        
        return stats
