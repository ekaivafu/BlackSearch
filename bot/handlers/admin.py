
# pyrefly: ignore [missing-import]
from aiogram import Router, F, Bot
# pyrefly: ignore [missing-import]
from aiogram.filters import Command
# pyrefly: ignore [missing-import]
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
# pyrefly: ignore [missing-import]
from aiogram.fsm.state import State, StatesGroup
# pyrefly: ignore [missing-import]
from aiogram.fsm.context import FSMContext
from bot.services.user_service import UserService
from bot.models.models import TransactionType, CreditTransaction, User
from bot.config import config
from bot.keyboards.inline import get_recharge_approval_keyboard

class AdminStates(StatesGroup):
    pass

router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
    
    user_service = UserService(session)
    pending_users = await user_service.get_pending_users()
    
    text = "Admin Dashboard\n\n"
    text += f"Pending user requests: {len(pending_users)}\n"
    # Further dashboard features can go here
    
    await message.answer(text)

@router.message(Command("addcredit"))
async def cmd_addcredit(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
    
    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("Usage: /addcredit <telegram_user_id> <amount>")
    
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        return await message.answer("Invalid user ID or amount.")
        
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(target_id)
    
    if not user:
        return await message.answer("User not found.")
        
    user.credits += amount
    tx = CreditTransaction(
        user_id=user.id,
        amount=amount,
        transaction_type=TransactionType.ADMIN_ADJUSTMENT,
        balance_after=user.credits,
        created_by=message.from_user.id
    )
    session.add(tx)
    await session.flush()
    await message.answer(f"Added {amount} credits to user {target_id}. New balance: {user.credits}")

@router.message(F.text == "⚙️ Manage Users")
async def btn_manage_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "**Manage Users**\n\n"
        "Currently, new user requests will be sent to you automatically for approval.\n\n"
        "To view the admin dashboard, type: `/admin`\n"
        "To view bot statistics, type: `/stats`\n"
        "To ban a user, type: `/ban <user_id>`\n"
        "To unban a user, type: `/unban <user_id>`\n"
        "To broadcast a message to all users, type: `/broadcast <message>`",
        parse_mode="Markdown"
    )

@router.message(F.text == "💰 Manage Points")
async def btn_manage_points(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "**Manage Points**\n\n"
        "To add credits to a user manually, type:\n"
        "`/addcredit <user_id> <amount>`\n\n"
        "Example: `/addcredit 123456789 50`\n\n"
        "When users request a recharge, a button will appear in this chat for you to approve.",
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("approve_"))
async def cb_approve_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    
    target_id = int(callback.data.split("_")[1])
    user_service = UserService(session)
    
    user = await user_service.approve_user(target_id, callback.from_user.id, config.initial_credits)
    if user:
        await callback.message.edit_text(f"User {target_id} approved.")
        try:
            await bot.send_message(target_id, f"Your access request has been approved! You have {config.initial_credits} credits.")
        except Exception:
            pass
    else:
        await callback.message.edit_text(f"Failed to approve user {target_id}. Already processed?")
    await callback.answer()

@router.callback_query(F.data.startswith("reject_"))
async def cb_reject_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    
    target_id = int(callback.data.split("_")[1])
    user_service = UserService(session)
    
    user = await user_service.reject_user(target_id, callback.from_user.id)
    if user:
        await callback.message.edit_text(f"User {target_id} rejected.")
        try:
            await bot.send_message(target_id, "Your access request has been rejected.")
        except Exception:
            pass
    else:
        await callback.message.edit_text(f"Failed to reject user {target_id}.")
    await callback.answer()

@router.callback_query(F.data.startswith("recharge_approve_"))
async def cb_approve_recharge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
        
    req_id = int(callback.data.split("_")[2])
    
    user_service = UserService(session)
    req = await user_service.get_recharge_request(req_id)
    if not req or req.status != RechargeStatus.PENDING:
        return await callback.message.edit_text(f"Recharge #{req_id} already processed or not found.")
        
    amount = req.requested_credits
    req = await user_service.approve_recharge(req_id, callback.from_user.id, amount)
    
    if req:
        if amount == -1: pkg = "1 Day Unlimited"
        elif amount == -7: pkg = "7 Days Unlimited"
        else: pkg = f"{amount} credits"
        
        await callback.message.edit_text(f"✅ Recharge #{req_id} approved for {pkg}.")
        user = await session.get(User, req.user_id)
        if user:
            try:
                await bot.send_message(user.telegram_user_id, f"✅ Your purchase for **{pkg}** was approved! Your account is upgraded.", parse_mode="Markdown")
            except Exception:
                pass
    else:
        await callback.message.edit_text(f"❌ Recharge #{req_id} could not be approved. (Already processed?)")
    await callback.answer()

@router.callback_query(F.data.startswith("recharge_reject_"))
async def cb_reject_recharge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
        
    req_id = int(callback.data.split("_")[2])
    user_service = UserService(session)
    
    req = await user_service.reject_recharge(req_id, callback.from_user.id)
    if req:
        await callback.message.edit_text(f"Recharge #{req_id} rejected.")
        user = await session.get(User, req.user_id)
        if user:
            try:
                await bot.send_message(user.telegram_user_id, "❌ Your recharge request was rejected.")
            except Exception:
                pass
    else:
        await callback.message.edit_text(f"Recharge #{req_id} already processed.")
    await callback.answer()

@router.message(Command("ban"))
async def cmd_ban(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
    
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("Usage: /ban <telegram_user_id>")
    
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("Invalid user ID.")
        
    user_service = UserService(session)
    success = await user_service.ban_user(target_id)
    if success:
        await message.answer(f"✅ User {target_id} has been banned.")
    else:
        await message.answer(f"❌ User not found.")

@router.message(Command("unban"))
async def cmd_unban(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
    
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("Usage: /unban <telegram_user_id>")
    
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("Invalid user ID.")
        
    user_service = UserService(session)
    success = await user_service.unban_user(target_id)
    if success:
        await message.answer(f"✅ User {target_id} has been unbanned.")
    else:
        await message.answer(f"❌ User not found or not banned.")

@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
        
    user_service = UserService(session)
    stats = await user_service.get_stats()
    
    text = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"✅ <b>Approved Users:</b> {stats['approved_users']}\n"
        f"🚫 <b>Banned Users:</b> {stats['banned_users']}\n\n"
        f"🔍 <b>Total Searches:</b> {stats['total_searches']}\n"
        f"💰 <b>Total Floating Credits:</b> {stats['total_credits']}"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, session: AsyncSession, bot: Bot):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Usage: /broadcast <your message>")
        
    msg_text = parts[1]
    user_service = UserService(session)
    users = await user_service.get_all_approved_users()
    
    sent = 0
    failed = 0
    await message.answer(f"Broadcasting to {len(users)} users...")
    
    for u in users:
        try:
            await bot.send_message(u.telegram_user_id, f"📢 <b>Announcement:</b>\n\n{msg_text}", parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
            
    await message.answer(f"✅ Broadcast complete.\nSent: {sent}\nFailed: {failed}")
