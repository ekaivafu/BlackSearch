from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from bot.services.user_service import UserService
from bot.services.search_service import SearchService
from bot.models.models import UserStatus
from bot.keyboards.inline import get_approval_keyboard, get_recharge_request_keyboard, get_recharge_amounts_keyboard, get_search_type_keyboard
from bot.keyboards.reply import get_main_keyboard
from bot.config import config
import asyncio

class SearchStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_aadhar = State()
    waiting_for_email = State()
    waiting_for_username = State()

search_queue_count = 0
search_lock = asyncio.Lock()

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, bot: Bot):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    
    if not user:
        user = await user_service.create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

    if user.status != UserStatus.APPROVED:
        await message.answer("Your account is not authorized.")
        return

    import datetime
    is_admin = message.from_user.id in config.admin_ids
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    if is_admin:
        credits_display = "Unlimited 👑"
    elif user.subscription_end and user.subscription_end > now_utc:
        credits_display = f"Unlimited until {user.subscription_end.strftime('%Y-%m-%d %H:%M')}"
    else:
        credits_display = str(user.credits)

    await message.answer(
        f"👋 <b>Welcome to BlackSearch!</b>\n\n💰 <b>Points/Credits:</b> <code>{credits_display}</code>\n\nPlease choose an option from the menu below:",
        reply_markup=get_main_keyboard(is_admin),
        parse_mode="HTML"
    )
    
    if not is_admin and user.credits == 0 and not (user.subscription_end and user.subscription_end > now_utc):
        await message.answer("⚠️ Your points are zero! Please click <b>💳 Request Recharge</b> below to buy a package.", parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "Available commands:\n"
        "/start - Register or check status\n"
        "/status - View account status\n"
        "/search <query> - Perform a search (costs 1 credit)\n"
        "/recharge - Request more credits\n"
        "/cancel - Cancel current operation"
    )
    if message.from_user.id in config.admin_ids:
        text += (
            "\n\nAdmin commands:\n"
            "/admin - Admin Dashboard\n"
            "/addcredit <user_id> <amount> - Add credits to a user manually"
        )
    await message.answer(text)

@router.message(Command("status"))
async def cmd_status(message: Message, session: AsyncSession):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    
    if not user:
        return await message.answer("Please type /start first.")
        
    is_admin = message.from_user.id in config.admin_ids
    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if is_admin:
        credits_display = "Unlimited 👑"
    elif user.subscription_end and user.subscription_end > now_utc:
        credits_display = f"Unlimited until {user.subscription_end.strftime('%Y-%m-%d %H:%M')} 👑"
    else:
        credits_display = str(user.credits)
    
    status_text = (
        f"📊 <b>Account Status</b>\n\n"
        f"🛡️ <b>Status:</b> <code>{user.status.value.upper()}</code>\n"
        f"💰 <b>Remaining Credits:</b> <code>{credits_display}</code>\n"
        f"🔍 <b>Total Searches:</b> <code>{user.total_searches}</code>\n"
        f"📅 <b>Created At:</b> <code>{user.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC</code>"
    )
    await message.answer(status_text, parse_mode="HTML")

@router.message(Command("search"))
async def cmd_search(message: Message, session: AsyncSession):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)

    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("You are not authorized to perform searches.")

    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    has_sub = user.subscription_end and user.subscription_end > now_utc

    if user.credits < 1 and not has_sub and message.from_user.id not in config.admin_ids:
        return await message.answer(
            "Your search credits are exhausted. Please click '💳 Request Recharge' to buy more."
        )
        
    await message.answer("Please select what you want to search by:", reply_markup=get_search_type_keyboard())

@router.message(F.text == "📱 Number Info")
async def btn_search_phone(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
    
    await message.answer("📱 <b>Please enter the Phone Number to search:</b>", parse_mode="HTML")
    await state.set_state(SearchStates.waiting_for_phone)

@router.message(F.text == "🪪 Aadhar Info")
async def btn_aadhar_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer("🪪 <b>Please enter the Aadhaar Number to search:</b>", parse_mode="HTML")
    await state.set_state(SearchStates.waiting_for_aadhar)

@router.message(F.text == "📧 Email Info")
async def btn_email_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer("📧 <b>Please enter the Email Address to search:</b>", parse_mode="HTML")
    await state.set_state(SearchStates.waiting_for_email)

@router.message(F.text == "👤 Username Info")
async def btn_username_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer("👤 <b>Please enter the Username to search:</b>", parse_mode="HTML")
    await state.set_state(SearchStates.waiting_for_username)

@router.message(F.text == "📖 How to use")
async def btn_how_to_use(message: Message):
    text = (
        "🕵️‍♂️ **Welcome to the BlackSearch OSINT Guide!**\n"
        "Here is how to use the powerful tools at your disposal:\n\n"
        "📱 **Number Info:** Enter any 10-digit phone number. The bot will cross-reference our private database and live Truecaller records to fetch names, addresses, and connected numbers!\n\n"
        "🪪 **Aadhar Info:** Enter a 12-digit Aadhar number to pull up deeply connected personal details from leaked governmental databases.\n\n"
        "📧 **Email Info:** Enter an email address. The bot will search for personal details AND stealthily ping 120+ social media sites to find active accounts linked to that email!\n\n"
        "👤 **Username Info:** Enter a gamer tag or username. We deploy our advanced intelligence engine to scan over 400 platforms across the entire internet to find every single profile matching that name!\n\n"
        "💳 **Request Recharge:** Run out of credits? Hit this to purchase a 1-day or 7-day Unlimited Plan from the Admin!"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "📊 My Status")
async def btn_status(message: Message, session: AsyncSession):
    await cmd_status(message, session)

@router.message(F.text == "💳 Request Recharge")
async def btn_recharge(message: Message, session: AsyncSession, bot: Bot):
    await cmd_recharge(message, session, bot)

@router.message(SearchStates.waiting_for_phone)
@router.message(SearchStates.waiting_for_aadhar)
@router.message(SearchStates.waiting_for_email)
@router.message(SearchStates.waiting_for_username)
async def process_search_input(message: Message, session: AsyncSession, state: FSMContext):
    query = message.text.strip()
    
    if query in ["📱 Number Info", "🪪 Aadhar Info", "📧 Email Info", "👤 Username Info", "📊 My Status", "📖 How to use", "💳 Request Recharge", "⚙️ Manage Users", "💰 Manage Points", "🔍 Telegram Info"]:
        await state.clear()
        is_admin = message.from_user.id in config.admin_ids
        return await message.answer("Search cancelled. Please select the option again to proceed.", reply_markup=get_main_keyboard(is_admin))
        
    current_state = await state.get_state()
    
    if current_state == SearchStates.waiting_for_phone.state:
        search_type = "phone"
        if not query.isdigit():
            return await message.answer("⚠️ Please write the number correctly without +91 or spaces, like this: 1234567890")
    elif current_state == SearchStates.waiting_for_email.state:
        search_type = "email"
        if " " in query or "@" not in query:
            return await message.answer("⚠️ Please write the email correctly without spaces, like this: example@gmail.com")
    elif current_state == SearchStates.waiting_for_username.state:
        search_type = "username"
        if " " in query:
            return await message.answer("⚠️ Please write the username correctly without spaces, like this: ekaivafu")
    else:
        search_type = "aadhar"
        if not query.isdigit():
            return await message.answer("⚠️ Please write the Aadhar number correctly without spaces, like this: 123456789012")
            
    await state.clear()
    
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("You are not authorized to perform searches.")
        
    is_admin = message.from_user.id in config.admin_ids
    
    if not is_admin:
        import datetime
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        has_sub = user.subscription_end and user.subscription_end > now_utc
        
        if user.credits < 1 and not has_sub:
            return await message.answer(
                "Your search credits are exhausted. Please request a recharge.",
                reply_markup=get_recharge_request_keyboard()
            )
            
        if not has_sub:
            # Deduct credit
            deducted = await user_service.deduct_credit(user.telegram_user_id, 1)
            if not deducted:
                return await message.answer("Failed to process credits. Please try again.")
        
    global search_queue_count
    search_queue_count += 1
    try:
        # Since MotherDuck can easily handle 15 active searches at once, the first 15 people are NOT in a queue!
        if search_queue_count > 15:
            wait_msg = await message.answer(f"⏳ <b>You are in a queue!</b>\nPeople ahead of you: {search_queue_count - 15}\n<i>I will notify you when your search is over.</i>", parse_mode="HTML")
        else:
            wait_msg = await message.answer("⏳ <b>Querying Global Database, please wait...</b>\n<code>[          ]</code>", parse_mode="HTML")
        
        search_service = SearchService(session)
        search_task = asyncio.create_task(search_service.search(user, query=query, search_type=search_type))
        
        frames = [
            "[=         ]",
            "[==        ]",
            "[===       ]",
            "[====      ]",
            "[=====     ]",
            "[======    ]",
            "[=======   ]",
            "[========  ]",
            "[========= ]",
            "[==========]"
        ]
        
        if search_queue_count > 15:
            animation_texts = [
                f"⏳ <b>You are in a queue!</b> ({search_queue_count - 15} ahead of you)\n<i>I will notify you when your search is over.</i>"
            ]
        elif search_type == "email":
            animation_texts = [
                "⏳ <b>Searching Personal Database...</b>",
                "⏳ <b>Scanning 120+ Social Media Sites...</b>",
                "⏳ <b>Checking Linked Accounts...</b>",
                "⏳ <b>Cross-referencing Open Source data...</b>"
            ]
        elif search_type == "username":
            animation_texts = [
                "⏳ <b>Initializing OSINT Engine...</b>",
                "⏳ <b>Scanning 400+ Social Media Platforms...</b>",
                "⏳ <b>Extracting Active Profiles...</b>",
                "⏳ <b>Cross-referencing Open Source data...</b>"
            ]
        else:
            animation_texts = [
                "⏳ <b>Querying Global Database, please wait...</b>"
            ]
        
        frame_idx = 0
        
        # Animate loading bar while search is running
        while not search_task.done():
            for _ in range(25): # check every 0.1s for 2.5s total before updating frame
                if search_task.done():
                    break
                await asyncio.sleep(0.1)
                
            if not search_task.done():
                frame_idx = (frame_idx + 1) % len(frames)
                text_idx = frame_idx % len(animation_texts)
                msg_text = animation_texts[text_idx]
                
                try:
                    await wait_msg.edit_text(f"{msg_text}\n<code>{frames[frame_idx]}</code>", parse_mode="HTML")
                except Exception:
                    pass
                    
        result = search_task.result()
        
        # Delete waiting message
        try:
            await wait_msg.delete()
        except Exception:
            pass
    finally:
        search_queue_count -= 1
    
    if result["success"]:
        import datetime
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if is_admin:
            credits_display = "Unlimited 👑"
        elif user.subscription_end and user.subscription_end > now_utc:
            credits_display = f"Unlimited ({user.subscription_end.strftime('%Y-%m-%d')})"
        else:
            credits_display = str(user.credits)
            
        await message.answer(
            f"✅ <b>Search Successful!</b>\n\n{result['data']}\n\n💰 <b>Remaining credits:</b> <code>{credits_display}</code>",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ {result.get('data', 'Search Failed: Unknown error')}", parse_mode="HTML")

from bot.keyboards.inline import get_payment_packages_keyboard

@router.message(Command("recharge"))
async def cmd_recharge(message: Message, session: AsyncSession, bot: Bot):
    if message.from_user.id in config.admin_ids:
        return await message.answer("You are an Admin! You have unlimited credits and do not need to recharge. 👑")
    
    text = (
        "👑 <b>Buy Credits or Unlimited Plans</b>\n\n"
        "To purchase, please contact the owner @tgekaiva.\n\n"
        "Select the package you want to buy below to send a purchase request to the admin:"
    )
    await message.answer(text, reply_markup=get_payment_packages_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "request_recharge")
async def cb_request_recharge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Handler for the 'Request Recharge' inline button shown when credits run out.
    Previously this button had no handler — clicking it did nothing.
    Now it opens the package selection menu.
    """
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await callback.answer("You are not authorized.", show_alert=True)

    if callback.from_user.id in config.admin_ids:
        return await callback.answer("You are an Admin with unlimited credits! 👑", show_alert=True)

    text = (
        "👑 <b>Buy Credits or Unlimited Plans</b>\n\n"
        "To purchase, please contact the owner @tgekaiva.\n\n"
        "Select the package you want to buy below to send a purchase request to the admin:"
    )
    await callback.message.answer(text, reply_markup=get_payment_packages_keyboard(), parse_mode="HTML")
    await callback.answer()  # dismiss the button spinner


@router.callback_query(F.data.startswith("buy_package_"))
async def cb_buy_package(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    package_val = int(callback.data.split("_")[2])
    
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await callback.answer("You are not authorized.", show_alert=True)
        
    req = await user_service.request_recharge(callback.from_user.id, package_val)
    if not req:
        return await callback.answer(
            "You already have a pending purchase request. Please wait for the admin to process it.",
            show_alert=True
        )
        
    if package_val == 15: pkg_name = "₹50 for 15 searches"
    elif package_val == 40: pkg_name = "₹100 for 40 searches"
    elif package_val == -1: pkg_name = "₹200 for 1 day unlimited"
    elif package_val == -7: pkg_name = "₹700 for 7 days unlimited"
    else: pkg_name = f"{package_val} credits"

    # ✅ CRITICAL FIX: answer the callback BEFORE editing the message.
    # Without this, Telegram shows a 30-second spinning loader then silently
    # fails — the user sees "nothing happen" even though the DB record was created.
    await callback.answer("✅ Request sent! Please contact @tgekaiva to pay.")

    await callback.message.edit_text(
        f"✅ <b>Purchase Request Sent!</b>\n\nYou selected: <b>{pkg_name}</b>\n\n"
        "👉 <b>Please message @tgekaiva to complete your payment.</b>\n"
        "Once payment is confirmed, your account will be upgraded instantly!",
        parse_mode="HTML"
    )
    
    name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "None"
    
    admin_text = (
        f"💳 <b>New Purchase Request #{req.id}</b>\n\n"
        f"👤 <b>Name:</b> <a href='tg://user?id={callback.from_user.id}'>{name}</a>\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🆔 <b>User ID:</b> <code>{callback.from_user.id}</code>\n\n"
        f"📦 <b>Package Selected:</b> {pkg_name}"
    )
    
    from bot.keyboards.inline import get_recharge_approval_keyboard
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                reply_markup=get_recharge_approval_keyboard(req.id),
                parse_mode="HTML"
            )
        except Exception:
            pass

@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    await message.answer("Cancelled current operation.", reply_markup=None)
