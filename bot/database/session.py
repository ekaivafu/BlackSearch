from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from bot.config import config
from bot.models.base import Base
import bot.models.models # Ensure all models are registered with Base.metadata

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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure plan_id column exists on recharge_requests if it was created prior
        try:
            await conn.execute(
                text("ALTER TABLE recharge_requests ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL;")
            )
        except Exception as e:
            # Not critical if already present or dialect doesn't support IF NOT EXISTS
            pass

    # Seed default plans and initial setting if needed
    async with async_session() as session:
        async with session.begin():
            plan_service = PlanService(session)
            await plan_service.seed_default_plans()
            # Ensure initial_credits setting exists
            cur_cred = await plan_service.get_setting("initial_credits", "")
            if not cur_cred:
                await plan_service.set_setting("initial_credits", str(config.initial_credits))

