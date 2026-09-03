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
    import asyncpg

    # Raw connection for DDL migrations runs outside transaction pooling to avoid lock conflicts
    raw_url = config.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        raw_conn = await asyncpg.connect(raw_url, ssl="require")
        try:
            await raw_conn.execute("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'DAILY_BONUS';")
        except Exception:
            pass
        try:
            await raw_conn.execute("ALTER TABLE recharge_requests ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL;")
            await raw_conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_credits INTEGER DEFAULT 0;")
            await raw_conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_credits_expire_at TIMESTAMP WITH TIME ZONE;")
            await raw_conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_date DATE;")
            await raw_conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_channel_verified BOOLEAN DEFAULT FALSE;")
        except Exception:
            pass
        finally:
            await raw_conn.close()
    except Exception as e:
        logger.debug(f"DDL check: {e}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default plans and initial setting if needed
    async with async_session() as session:
        async with session.begin():
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

