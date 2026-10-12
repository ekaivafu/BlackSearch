import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.models.base import Base
from bot.services.user_service import UserService
from bot.models.models import UserStatus

@pytest_asyncio.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.mark.asyncio
async def test_create_user(async_session):
    svc = UserService(async_session)
    user = await svc.create_user(12345, "testuser", "Test", "User")
    assert user.telegram_user_id == 12345
    assert user.status == UserStatus.APPROVED
    assert user.credits >= 0

@pytest.mark.asyncio
async def test_search_deduction(async_session):
    svc = UserService(async_session)
    user = await svc.create_user(12345, "testuser", "Test", "User")
    user.credits = 1
    user.bonus_credits = 0
    await async_session.flush()
    
    # Successful deduction returns (True, deduction_info)
    res, info = await svc.deduct_credit(12345, 1)
    assert res is True
    
    user = await svc.get_user_by_telegram_id(12345)
    assert user.credits == 0
    
    # Failed deduction when 0 credits
    res2, info2 = await svc.deduct_credit(12345, 1)
    assert res2 is False
