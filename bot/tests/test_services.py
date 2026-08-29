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
    assert user.status == UserStatus.PENDING
    assert user.credits == 0

@pytest.mark.asyncio
async def test_approve_user(async_session):
    svc = UserService(async_session)
    await svc.create_user(12345, "testuser", "Test", "User")
    
    user = await svc.approve_user(12345, admin_id=999, initial_credits=10)
    assert user.status == UserStatus.APPROVED
    assert user.credits == 10
    assert user.approved_by == 999

@pytest.mark.asyncio
async def test_search_deduction(async_session):
    svc = UserService(async_session)
    await svc.create_user(12345, "testuser", "Test", "User")
    await svc.approve_user(12345, admin_id=999, initial_credits=1)
    
    # Successful deduction
    res = await svc.deduct_credit(12345, 1)
    assert res is True
    
    user = await svc.get_user_by_telegram_id(12345)
    assert user.credits == 0
    
    # Failed deduction
    res2 = await svc.deduct_credit(12345, 1)
    assert res2 is False
