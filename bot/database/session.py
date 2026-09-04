import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from bot.config import config
from bot.models.base import Base
import bot.models.models # Ensure all models are registered with Base.metadata

logger = logging.getLogger(__name__)

engine = create_async_engine(
    config.database_url,
    pool_size=10,
    max_overflow=20,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session

async def init_db():
    """Create tables if not existing, ensure schema migrations, and seed default plans."""
    from bot.services.plan_service import PlanService

    # DDL migrations run with AUTOCOMMIT isolation level to allow ALTER TYPE / ALTER TABLE
    try:
        async with engine.connect() as conn:
            ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
            try:
                await ac.execute(text("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'DAILY_BONUS';"))
                await ac.execute(text("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'REFERRAL';"))
            except Exception:
                pass
            try:
                await ac.execute(text("ALTER TABLE recharge_requests ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_credits INTEGER DEFAULT 0;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_credits_expire_at TIMESTAMP WITH TIME ZONE;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_date DATE;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_channel_verified BOOLEAN DEFAULT FALSE;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_reward_claimed BOOLEAN DEFAULT FALSE;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0;"))
                await ac.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_credits_earned INTEGER DEFAULT 0;"))
                await ac.execute(text("""
                    CREATE TABLE IF NOT EXISTS blacklists (
                        id SERIAL PRIMARY KEY,
                        target_type VARCHAR(20) NOT NULL,
                        value VARCHAR(255) NOT NULL,
                        note VARCHAR(255),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        created_by BIGINT
                    );
                    CREATE INDEX IF NOT EXISTS ix_blacklists_target_type ON blacklists (target_type);
                    CREATE INDEX IF NOT EXISTS ix_blacklists_value ON blacklists (value);
                """))
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"DDL migration check: {e}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default plans and initial setting if needed
    async with async_session() as session:
        plan_service = PlanService(session)
        await plan_service.seed_default_plans()
        # Ensure initial_credits setting exists
        cur_cred = await plan_service.get_setting("initial_credits", "")
        if not cur_cred:
            await plan_service.set_setting("initial_credits", str(config.initial_credits))
        # Ensure daily_bonus_credits setting exists
        cur_bonus = await plan_service.get_setting("daily_bonus_credits", "")
        if not cur_bonus:
            await plan_service.set_setting("daily_bonus_credits", "3")
        # Ensure referral_reward_credits setting exists
        cur_ref = await plan_service.get_setting("referral_reward_credits", "")
        if not cur_ref:
            await plan_service.set_setting("referral_reward_credits", "2")
        await session.commit()

