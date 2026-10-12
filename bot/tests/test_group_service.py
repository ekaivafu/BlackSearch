import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.models.base import Base
from bot.models.models import ManagedGroup
from bot.services.group_service import GroupService
from bot.middleware.group_control import GroupControlMiddleware
from bot.keyboards.inline import get_admin_groups_keyboard, get_groups_delete_keyboard

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
    GroupService.clear_cache()

@pytest.mark.asyncio
async def test_get_or_create_group(async_session: AsyncSession):
    svc = GroupService(async_session)
    group = await svc.get_or_create_group(
        chat_id=-1001234567890,
        title="Test Alpha Group",
        username="alphagroup"
    )
    assert group.id is not None
    assert group.chat_id == -1001234567890
    assert group.title == "Test Alpha Group"
    assert group.is_active is True
    # Verify cached
    assert GroupService.get_cached_group_status(-1001234567890) is True

    # Re-fetch should update title if changed and keep is_active
    group2 = await svc.get_or_create_group(
        chat_id=-1001234567890,
        title="Updated Alpha Group"
    )
    assert group2.id == group.id
    assert group2.title == "Updated Alpha Group"
    assert group2.is_active is True

@pytest.mark.asyncio
async def test_toggle_group_status(async_session: AsyncSession):
    svc = GroupService(async_session)
    group = await svc.get_or_create_group(chat_id=-100999, title="Toggle Group")
    assert group.is_active is True

    # Toggle to OFF (Ghost mode)
    success, toggled = await svc.toggle_group_status(group.id)
    assert success is True
    assert toggled.is_active is False
    assert GroupService.get_cached_group_status(-100999) is False

    # Toggle back to ON
    success, toggled2 = await svc.toggle_group_status(group.id)
    assert success is True
    assert toggled2.is_active is True
    assert GroupService.get_cached_group_status(-100999) is True

@pytest.mark.asyncio
async def test_set_all_groups_status(async_session: AsyncSession):
    svc = GroupService(async_session)
    await svc.get_or_create_group(chat_id=-1001, title="Group 1")
    await svc.get_or_create_group(chat_id=-1002, title="Group 2")

    # Turn all OFF
    count = await svc.set_all_groups_status(False)
    assert count == 2
    assert GroupService.get_cached_group_status(-1001) is False
    assert GroupService.get_cached_group_status(-1002) is False

    stats = await svc.get_stats()
    assert stats["total"] == 2
    assert stats["active"] == 0
    assert stats["ghosted"] == 2

    # Turn all ON
    count = await svc.set_all_groups_status(True)
    assert count == 2
    assert GroupService.get_cached_group_status(-1001) is True
    assert GroupService.get_cached_group_status(-1002) is True

@pytest.mark.asyncio
async def test_delete_group(async_session: AsyncSession):
    svc = GroupService(async_session)
    group = await svc.get_or_create_group(chat_id=-100555, title="To Delete")
    assert GroupService.get_cached_group_status(-100555) is True

    deleted, title = await svc.delete_group(group.id)
    assert deleted is True
    assert title == "To Delete"
    assert GroupService.get_cached_group_status(-100555) is None

@pytest.mark.asyncio
async def test_group_control_middleware_ghosting(async_session: AsyncSession):
    middleware = GroupControlMiddleware()
    svc = GroupService(async_session)

    # 1. Private chat passes through
    handler_mock = AsyncMock(return_value="OK")
    msg_private = MagicMock()
    msg_private.chat.type = "private"
    msg_private.chat.id = 12345
    msg_private.from_user.id = 12345

    res = await middleware(handler_mock, msg_private, {"session": async_session})
    assert res == "OK"
    assert handler_mock.call_count == 1

    # 2. Group chat when active (True) -> passes through
    group = await svc.get_or_create_group(chat_id=-100777, title="Active Group")
    handler_mock.reset_mock()

    msg_group = MagicMock()
    msg_group.chat.type = "supergroup"
    msg_group.chat.id = -100777
    msg_group.chat.title = "Active Group"
    msg_group.chat.username = "activegroup"
    msg_group.from_user.id = 555

    res = await middleware(handler_mock, msg_group, {"session": async_session})
    assert res == "OK"
    assert handler_mock.call_count == 1

    # 3. Group chat when OFF (Ghost Mode) -> drops update (returns None, handler NOT called!)
    await svc.set_group_status(group.id, False)
    handler_mock.reset_mock()

    res = await middleware(handler_mock, msg_group, {"session": async_session})
    assert res is None  # GHOSTED!
    assert handler_mock.call_count == 0  # Handler was never reached!

@pytest.mark.asyncio
async def test_admin_groups_keyboard(async_session: AsyncSession):
    svc = GroupService(async_session)
    g1 = await svc.get_or_create_group(chat_id=-101, title="Alpha Group")
    g2 = await svc.get_or_create_group(chat_id=-102, title="Beta Group")
    await svc.set_group_status(g2.id, False)

    groups = await svc.get_all_groups()
    kb = get_admin_groups_keyboard(groups, page=1)
    assert kb is not None
    # Verify button texts include status indicators
    buttons_text = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("🟢 Alpha Group (ON)" in b for b in buttons_text)
    assert any("🔴 Beta Group (OFF/Ghost)" in b for b in buttons_text)
    assert "⚡ Turn ALL ON" in buttons_text
    assert "👻 Ghost ALL (OFF)" in buttons_text
    assert "➕ Add Group ID" in buttons_text
