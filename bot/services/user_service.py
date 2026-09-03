import logging
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from bot.models.models import User, UserStatus, CreditTransaction, TransactionType, RechargeRequest, RechargeStatus, SearchLog
import datetime

logger = logging.getLogger(__name__)

class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_user_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(self, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None) -> User:
        from bot.services.plan_service import PlanService
        ps = PlanService(self.session)
        init_credits = await ps.get_initial_credits()

        user = User(
            telegram_user_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            status=UserStatus.APPROVED,
            credits=init_credits
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

        return user

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
            balance_after=initial_credits,
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

        if user.credits < amount:
            return False

        user.credits -= amount
        
        tx = CreditTransaction(
            user_id=user.id,
            amount=-amount,
            transaction_type=TransactionType.SEARCH,
            balance_after=user.credits,
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
