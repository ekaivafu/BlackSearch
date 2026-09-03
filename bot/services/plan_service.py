import logging
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from bot.models.models import Plan, PlanType, BotSetting
from bot.config import config

logger = logging.getLogger(__name__)

DEFAULT_PLANS = [
    {
        "name": "₹50 for 15 searches",
        "plan_type": PlanType.CREDITS,
        "credits": 15,
        "days": 0,
        "price": 50,
    },
    {
        "name": "₹100 for 40 searches",
        "plan_type": PlanType.CREDITS,
        "credits": 40,
        "days": 0,
        "price": 100,
    },
    {
        "name": "₹200 for 1 day unlimited",
        "plan_type": PlanType.DAYS,
        "credits": 0,
        "days": 1,
        "price": 200,
    },
    {
        "name": "₹700 for 7 days unlimited",
        "plan_type": PlanType.DAYS,
        "credits": 0,
        "days": 7,
        "price": 700,
    },
]

class PlanService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_plans(self, active_only: bool = True) -> List[Plan]:
        """Fetch all plans. If active_only is True, return only active plans."""
        stmt = select(Plan)
        if active_only:
            stmt = stmt.where(Plan.is_active == True)
        stmt = stmt.order_by(Plan.price.asc(), Plan.id.asc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_plan_by_id(self, plan_id: int) -> Optional[Plan]:
        """Get a single plan by its ID."""
        stmt = select(Plan).where(Plan.id == plan_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create_plan(
        self,
        name: str,
        plan_type: PlanType,
        credits: int = 0,
        days: int = 0,
        price: int = 0,
    ) -> Plan:
        """Create a new subscription or credit plan."""
        plan = Plan(
            name=name.strip(),
            plan_type=plan_type,
            credits=max(0, credits),
            days=max(0, days),
            price=max(0, price),
            is_active=True,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def update_plan(self, plan_id: int, **kwargs) -> Optional[Plan]:
        """Update fields of an existing plan."""
        plan = await self.get_plan_by_id(plan_id)
        if not plan:
            return None
        for key, value in kwargs.items():
            if hasattr(plan, key):
                setattr(plan, key, value)
        await self.session.flush()
        return plan

    async def delete_plan(self, plan_id: int) -> bool:
        """Soft-delete a plan by setting is_active to False."""
        plan = await self.get_plan_by_id(plan_id)
        if not plan:
            return False
        plan.is_active = False
        await self.session.flush()
        return True

    async def seed_default_plans(self) -> None:
        """Seed the 4 default plans into the database if no plans exist."""
        stmt = select(func.count(Plan.id))
        count = (await self.session.execute(stmt)).scalar() or 0
        if count == 0:
            for item in DEFAULT_PLANS:
                plan = Plan(
                    name=item["name"],
                    plan_type=item["plan_type"],
                    credits=item["credits"],
                    days=item["days"],
                    price=item["price"],
                    is_active=True,
                )
                self.session.add(plan)
            await self.session.flush()
            logger.info("Default plans seeded into database.")

    # ── Settings (e.g. Free Credits for New Users) ───────────────────────────
    async def get_setting(self, key: str, default: str = "") -> str:
        """Fetch a system setting value by key."""
        stmt = select(BotSetting).where(BotSetting.key == key)
        res = await self.session.execute(stmt)
        setting = res.scalar_one_or_none()
        if setting:
            return setting.value
        return default

    async def set_setting(self, key: str, value: str) -> None:
        """Set or update a system setting value."""
        stmt = select(BotSetting).where(BotSetting.key == key)
        res = await self.session.execute(stmt)
        setting = res.scalar_one_or_none()
        if setting:
            setting.value = str(value)
        else:
            setting = BotSetting(key=key, value=str(value))
            self.session.add(setting)
        await self.session.flush()

    async def get_initial_credits(self) -> int:
        """Get the free credits granted to new registered/approved users."""
        raw = await self.get_setting("initial_credits", str(config.initial_credits))
        try:
            return int(raw)
        except (ValueError, TypeError):
            return config.initial_credits

    async def set_initial_credits(self, amount: int) -> None:
        """Update the free credits for new users."""
        await self.set_setting("initial_credits", str(max(0, amount)))

    async def get_daily_bonus_credits(self) -> int:
        """Get the daily bonus credits granted on the 1st message of each day (expires at 23:59)."""
        raw = await self.get_setting("daily_bonus_credits", "3")
        try:
            return int(raw)
        except (ValueError, TypeError):
            return 3

    async def set_daily_bonus_credits(self, amount: int) -> None:
        """Update the daily bonus credits amount."""
        await self.set_setting("daily_bonus_credits", str(max(0, amount)))
