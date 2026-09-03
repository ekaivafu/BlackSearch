from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from bot.services.user_service import UserService
from bot.services.search_service import SearchService
from bot.models.models import User, UserStatus
from bot.keyboards.inline import get_approval_keyboard, get_recharge_request_keyboard, get_recharge_amounts_keyboard, get_search_type_keyboard
from bot.keyboards.reply import get_main_keyboard
from bot.config import config
from bot.middleware.daily_bonus import DAILY_BONUS_BANNER
import asyncio

class SearchStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_aadhar = State()
    waiting_for_email = State()
    waiting_for_username = State()

search_queue_count = 0
search_lock = asyncio.Lock()

router = Router()

def build_welcome_text(user: User, is_admin: bool) -> str:
    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    effective_credits = UserService.get_effective_credits(user)

    if is_admin:
        quota_display = "♾️ Unlimited 👑"
        tier_display = "👑 System Administrator"
    elif user.subscription_end and user.subscription_end > now_utc:
        diff = user.subscription_end - now_utc
        days = diff.days
        hours = diff.seconds // 3600
        quota_display = f"♾️ Unlimited ({days}d {hours}h left)"
        tier_display = "👑 VIP Unlimited Pass"
    elif effective_credits > 0:
        parts = []
        if user.bonus_credits > 0:
            parts.append(f"🎁 {user.bonus_credits} daily (expires 23:59 IST)")
        if user.credits > 0:
            parts.append(f"🪙 {user.credits} permanent")
        quota_display = " | ".join(parts) if parts else str(effective_credits)
        tier_display = "🪙 Standard Operator"
    else:
        quota_display = "⚠️ 0 credits (Exhausted)"
        tier_display = "⏳ Daily Bonus Expired"

    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Operator"

    return (
        "⚡ <b>BLACKSEARCH OSINT INTELLIGENCE</b> ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{name}</b>!\n"
        "Your covert identity scanner and intelligence terminal.\n\n"
        "👤 <b>Account Overview:</b>\n"
        f"├ 🆔 <b>ID:</b> <code>{user.telegram_user_id}</code>\n"
        f"├ 🛡️ <b>Status:</b> <code>{user.status.value.upper()}</code>\n"
        f"├ 💎 <b>Tier:</b> {tier_display}\n"
        f"└ 💰 <b>Search Quota:</b> <b>{quota_display}</b>\n\n"
        "🚀 <b>Core Reconnaissance Modules:</b>\n"
        "• 📱 <b>Number Info</b> — Reverse carrier, Truecaller & leaked datasets\n"
        "• 🪪 <b>Aadhar Info</b> — Citizen demographics & linked registries\n"
        "• 📧 <b>Email Info</b> — Breach intelligence & 120+ social account scan\n"
        "• 👤 <b>Username Info</b> — Global footprint scanner across 400+ platforms\n\n"
        "👇 <i>Select an option from the menu below to begin:</i>"
    )

@router.callback_query(F.data == "verify_sub")
async def cb_verify_sub(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    from bot.services.channel_service import ChannelService
    from bot.keyboards.inline import get_force_sub_keyboard
    cs = ChannelService(session)
    missing = await cs.get_missing_channels(bot, callback.from_user.id)
    if missing:
        await callback.answer("❌ You haven't joined all required channels yet! Please join and try again.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=get_force_sub_keyboard(missing))
        except Exception:
            pass
        return

    # Success! User joined all channels.
    await callback.answer("🎉 Verification successful! Access granted.", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass

    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    is_new = False
    if not user:
        user = await user_service.create_user(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name
        )
        is_new = True

    # Alert admin about new verified user
    await ChannelService.notify_admins_new_user_verified(bot, session, user)

    # Process referral reward for referrer (if friend joined & verified)
    await user_service.process_referral_reward(bot, user)

    if is_new and user.bonus_credits > 0:
        await callback.message.answer(
            DAILY_BONUS_BANNER.format(amt=user.bonus_credits),
            parse_mode="HTML"
        )
    else:
        granted, amt, _ = await user_service.check_and_apply_daily_bonus(user)
        if granted:
            await callback.message.answer(
                DAILY_BONUS_BANNER.format(amt=amt),
                parse_mode="HTML"
            )

    is_admin = callback.from_user.id in config.admin_ids
    text = build_welcome_text(user, is_admin)
    await callback.message.answer(text, reply_markup=get_main_keyboard(is_admin), parse_mode="HTML")

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, bot: Bot):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    is_new = False
    
    if not user:
        # Check if started via referral link: e.g. /start ref_123456 or /start 123456
        referrer_id = None
        parts = (message.text or "").split()
        if len(parts) > 1:
            raw_ref = parts[1].strip()
            if raw_ref.startswith("ref_"):
                raw_ref = raw_ref[4:]
            if raw_ref.isdigit():
                parsed_ref = int(raw_ref)
                if parsed_ref != message.from_user.id:
                    referrer_id = parsed_ref

        user = await user_service.create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            referred_by=referrer_id
        )
        is_new = True

    if user.status != UserStatus.APPROVED:
        return await message.answer("🔒 Your account is pending authorization or has been suspended.")

    is_admin = message.from_user.id in config.admin_ids

    # Check force subscription for non-admins
    if not is_admin:
        from bot.services.channel_service import ChannelService
        from bot.keyboards.inline import get_force_sub_keyboard
        from bot.middleware.forcesub import RESTRICTED_TEXT
        cs = ChannelService(session)
        missing = await cs.get_missing_channels(bot, message.from_user.id)
        if missing:
            return await message.answer(RESTRICTED_TEXT, reply_markup=get_force_sub_keyboard(missing), parse_mode="HTML")

    # If user has joined all channels and not yet marked verified, alert admin
    if not user.is_channel_verified:
        from bot.services.channel_service import ChannelService
        await ChannelService.notify_admins_new_user_verified(bot, session, user)

    # Process referral reward if user was referred and is now verified
    await user_service.process_referral_reward(bot, user)

    if is_new and user.bonus_credits > 0:
        await message.answer(
            DAILY_BONUS_BANNER.format(amt=user.bonus_credits),
            parse_mode="HTML"
        )

    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    effective_credits = UserService.get_effective_credits(user)
    welcome_text = build_welcome_text(user, is_admin)

    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(is_admin),
        parse_mode="HTML"
    )
    
    if not is_admin and effective_credits == 0 and not (user.subscription_end and user.subscription_end > now_utc):
        await message.answer(
            "⚠️ <b>Notice:</b> You have <b>0 search credits</b> remaining.\n"
            "Click <b>💳 Request Recharge</b> below to purchase search packs or activate Unlimited VIP access!",
            parse_mode="HTML"
        )

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📖 <b>BLACKSEARCH FIELD MANUAL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>User Commands:</b>\n"
        "• /start — Launch dashboard & refresh profile\n"
        "• /status — View credits, active VIP pass & usage metrics\n"
        "• /refer — Refer & Earn dashboard & get invite link\n"
        "• /search — Open interactive search selector\n"
        "• /recharge — Browse subscription packages & request top-ups\n"
        "• /help — Show this manual\n"
        "• /cancel — Terminate any ongoing search prompt\n\n"
        "💡 <b>Pro-Tips:</b>\n"
        "• You get a daily bonus every day on your first message!\n"
        "• Share your referral link (/refer) to earn permanent search credits!\n"
        "• Phone searches run Truecaller & internal database lookups in parallel.\n"
        "• Email searches scan 120+ platforms to find linked social accounts."
    )
    if message.from_user.id in config.admin_ids:
        text += (
            "\n\n<b>Admin Commands:</b>\n"
            "• /admin — Administrator dashboard\n"
            "• /plans — Manage subscription & credit packages\n"
            "• /channels — Manage force-sub channels\n"
            "• /dailybonus — Change daily free bonus credits\n"
            "• /referralreward — Set referral reward credits\n"
            "• /deleteuser &lt;id&gt; — Wipe user from DB to test restart\n"
            "• /broadcast &lt;msg&gt; — Send message to all users"
        )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("status"))
async def cmd_status(message: Message, session: AsyncSession):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    
    if not user:
        return await message.answer("Please type /start first.")

    await user_service.check_and_apply_daily_bonus(user)
    effective_credits = UserService.get_effective_credits(user)
    is_admin = message.from_user.id in config.admin_ids
    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    if is_admin:
        credits_display = "♾️ Unlimited 👑"
        plan_badge = "👑 System Administrator"
    elif user.subscription_end and user.subscription_end > now_utc:
        diff = user.subscription_end - now_utc
        days = diff.days
        hours = diff.seconds // 3600
        credits_display = f"♾️ Unlimited ({days}d {hours}h left)"
        plan_badge = "👑 VIP Unlimited Pass"
    elif effective_credits > 0:
        parts = []
        if user.bonus_credits > 0:
            parts.append(f"🎁 {user.bonus_credits} daily (expires 23:59 IST)")
        if user.credits > 0:
            parts.append(f"🪙 {user.credits} permanent")
        credits_display = " | ".join(parts) if parts else str(effective_credits)
        plan_badge = "🪙 Standard Operator"
    else:
        credits_display = "⚠️ 0 credits (Exhausted)"
        plan_badge = "⏳ Daily Bonus Expired"

    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Operator"
    username_part = f"(@{user.username})" if user.username else ""
    created_date = user.created_at.strftime('%d %b %Y, %H:%M UTC') if user.created_at else "Unknown"

    status_text = (
        "📊 <b>OPERATOR STATUS REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Operator:</b> {name} {username_part}\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.telegram_user_id}</code>\n"
        f"🛡️ <b>Account Status:</b> <code>{user.status.value.upper()}</code>\n\n"
        "💳 <b>Subscription & Quota:</b>\n"
        f"├ 💎 <b>Tier:</b> {plan_badge}\n"
        f"└ 💰 <b>Search Balance:</b> <b>{credits_display}</b>\n\n"
        "📈 <b>Usage Metrics:</b>\n"
        f"├ 🔍 <b>Searches Executed:</b> <code>{user.total_searches}</code> queries\n"
        f"├ 👥 <b>Friends Referred:</b> <code>{user.referral_count or 0}</code> verified users\n"
        f"└ 📅 <b>Member Since:</b> <code>{created_date}</code>"
    )
    await message.answer(status_text, parse_mode="HTML")

def build_referral_text(user: User, bot_username: str, reward: int) -> str:
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.telegram_user_id}"
    ref_count = user.referral_count or 0
    earned_credits = user.referral_credits_earned or 0

    return (
        "👥 <b>REFER & EARN FREE SEARCH CREDITS</b> 👥\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Invite your friends to <b>BlackSearch OSINT Terminal</b> and earn permanent search credits for every verified friend who joins!\n\n"
        "🔗 <b>Your Exclusive Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "📊 <b>Your Referral Performance:</b>\n"
        f"├ 👥 <b>Verified Friends:</b> <code>{ref_count}</code> users\n"
        f"├ 💰 <b>Total Earned:</b> <code>+{earned_credits}</code> permanent credits\n"
        f"└ 🎁 <b>Reward per Referral:</b> <b>+{reward} Credits</b>\n\n"
        "🛡️ <b>Anti-Abuse Verification Rules:</b>\n"
        "1. Your friend must join using your unique referral link.\n"
        "2. Your friend <b>must have a public Telegram username</b>.\n"
        "3. Your friend <b>must join our required channel(s) and tap verify</b>.\n\n"
        "<i>⚡ Credits are added automatically to your permanent balance the moment your friend verifies!</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

@router.message(F.text == "👥 Refer & Earn")
@router.message(Command("refer"))
@router.message(Command("referral"))
async def cmd_referral(message: Message, session: AsyncSession, bot: Bot):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user:
        return await message.answer("Please type /start first.")

    from bot.services.plan_service import PlanService
    from bot.keyboards.inline import get_referral_keyboard
    ps = PlanService(session)
    reward = await ps.get_referral_reward_credits()

    bot_me = await bot.get_me()
    bot_username = bot_me.username or "BlackSearchBot"

    text = build_referral_text(user, bot_username, reward)
    await message.answer(text, reply_markup=get_referral_keyboard(bot_username, user.telegram_user_id), parse_mode="HTML")

@router.callback_query(F.data == "ref_refresh")
async def cb_referral_refresh(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    if not user:
        return await callback.answer("User not found.")

    from bot.services.plan_service import PlanService
    from bot.keyboards.inline import get_referral_keyboard
    ps = PlanService(session)
    reward = await ps.get_referral_reward_credits()

    bot_me = await bot.get_me()
    bot_username = bot_me.username or "BlackSearchBot"

    text = build_referral_text(user, bot_username, reward)
    try:
        await callback.message.edit_text(text, reply_markup=get_referral_keyboard(bot_username, user.telegram_user_id), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Referral stats refreshed!")

@router.message(Command("search"))
async def cmd_search(message: Message, session: AsyncSession):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)

    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("🔒 You are not authorized to perform searches.")

    await user_service.check_and_apply_daily_bonus(user)
    effective_credits = UserService.get_effective_credits(user)

    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    has_sub = user.subscription_end and user.subscription_end > now_utc

    if effective_credits < 1 and not has_sub and message.from_user.id not in config.admin_ids:
        return await message.answer(
            "⚠️ <b>Search Quota Exhausted!</b>\n\n"
            "Please click <b>💳 Request Recharge</b> below to top up credits or activate an Unlimited VIP pass.\n"
            "<i>(Or return tomorrow for your daily bonus credits!)</i>",
            parse_mode="HTML"
        )
        
    await message.answer(
        "🔍 <b>Select a reconnaissance module:</b>",
        reply_markup=get_search_type_keyboard(),
        parse_mode="HTML"
    )

@router.message(F.text == "📱 Number Info")
async def btn_search_phone(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
    
    await message.answer(
        "📱 <b>Phone Number Lookup Module</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Enter the <b>10-digit Phone Number</b> to investigate:\n\n"
        "👉 <i>Format: <code>9876543210</code> (no +91, no spaces)</i>\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.waiting_for_phone)

@router.message(F.text == "🪪 Aadhar Info")
async def btn_aadhar_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer(
        "🪪 <b>Aadhaar Identity Lookup Module</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Enter the <b>12-digit Aadhaar Number</b> to investigate:\n\n"
        "👉 <i>Format: <code>123456789012</code> (numbers only, no spaces)</i>\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.waiting_for_aadhar)

@router.message(F.text == "📧 Email Info")
async def btn_email_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer(
        "📧 <b>Email OSINT & Account Scanner</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Enter the <b>Target Email Address</b> to scan:\n\n"
        "👉 <i>Format: <code>target@gmail.com</code></i>\n"
        "<i>The engine will search database archives and check 120+ platforms.</i>\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.waiting_for_email)

@router.message(F.text == "👤 Username Info")
async def btn_username_search(message: Message, session: AsyncSession, state: FSMContext):
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(message.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await message.answer("❌ <b>You are not authorized.</b>", parse_mode="HTML")
        
    await message.answer(
        "👤 <b>Username Global Footprint Scanner</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Enter the <b>Username / Handle</b> to track:\n\n"
        "👉 <i>Format: <code>cyberrecon</code> (no @, no spaces)</i>\n"
        "<i>Deploys Sherlock recon across 400+ online platforms worldwide.</i>\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.waiting_for_username)

@router.message(F.text == "📖 How to use")
async def btn_how_to_use(message: Message):
    text = (
        "🕵️‍♂️ <b>BLACKSEARCH OSINT FIELD GUIDE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Master the 4 powerful search modules at your disposal:\n\n"
        "📱 <b>1. Number Lookup</b>\n"
        "• <b>What it does:</b> Cross-references Truecaller API, telecom records, and internal archives.\n"
        "• <b>Format:</b> 10-digit number without country code or spaces.\n"
        "• <b>Example:</b> <code>9876543210</code>\n\n"
        "🪪 <b>2. Aadhaar Lookup</b>\n"
        "• <b>What it does:</b> Pulls deeply linked citizen identity and governmental leak datasets.\n"
        "• <b>Format:</b> 12-digit Aadhaar number.\n"
        "• <b>Example:</b> <code>123456789012</code>\n\n"
        "📧 <b>3. Email OSINT & Social Footprint</b>\n"
        "• <b>What it does:</b> Searches leaks and scans 120+ platforms (Discord, Spotify, GitHub, etc.) to locate active accounts.\n"
        "• <b>Format:</b> Complete email address.\n"
        "• <b>Example:</b> <code>target@gmail.com</code>\n\n"
        "👤 <b>4. Username Global Recon</b>\n"
        "• <b>What it does:</b> Deploys our Sherlock engine to scan 400+ social networks and forums worldwide.\n"
        "• <b>Format:</b> Username handle without spaces.\n"
        "• <b>Example:</b> <code>cyberrecon</code>\n\n"
        "💰 <b>Need More Quota?</b>\n"
        "Click <b>💳 Request Recharge</b> below to activate instant credits or an Unlimited VIP Pass."
    )
    await message.answer(text, parse_mode="HTML")

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
    
    NAV_BUTTONS = [
        "📱 Number Info", "🪪 Aadhar Info", "📧 Email Info", "👤 Username Info",
        "📊 My Status", "📖 How to use", "💳 Request Recharge",
        "⚙️ Manage Users", "💰 Manage Points", "📦 Manage Plans", "📢 Channels", "🔍 Telegram Info"
    ]
    if query in NAV_BUTTONS:
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
        has_sub = bool(user.subscription_end and user.subscription_end > now_utc)
        await user_service.check_and_apply_daily_bonus(user)
        effective_credits = UserService.get_effective_credits(user)
        
        if effective_credits < 1 and not has_sub:
            return await message.answer(
                "⚠️ Your search credits are exhausted. Please request a recharge or wait for tomorrow's daily bonus.",
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
            effective_credits = UserService.get_effective_credits(user)
            parts = []
            if user.bonus_credits > 0:
                parts.append(f"🎁 {user.bonus_credits} daily")
            if user.credits > 0:
                parts.append(f"🪙 {user.credits} permanent")
            credits_display = " | ".join(parts) if parts else "0"
            
        await message.answer(
            f"✅ <b>Search Successful!</b>\n\n{result['data']}\n\n💰 <b>Remaining credits:</b> <code>{credits_display}</code>",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"❌ {result.get('data', 'Search Failed: Unknown error')}", parse_mode="HTML")

from bot.keyboards.inline import get_payment_packages_keyboard
from bot.services.plan_service import PlanService
from bot.models.models import PlanType

@router.message(Command("recharge"))
async def cmd_recharge(message: Message, session: AsyncSession, bot: Bot):
    if message.from_user.id in config.admin_ids:
        return await message.answer("You are an Admin! You have unlimited credits and do not need to recharge. 👑")
    
    plan_service = PlanService(session)
    plans = await plan_service.get_all_plans(active_only=True)
    
    text = (
        "👑 <b>Buy Credits or Unlimited Plans</b>\n\n"
        "To purchase, please contact the owner @tgekaiva.\n\n"
        "Select the package you want to buy below to send a purchase request to the admin:"
    )
    await message.answer(text, reply_markup=get_payment_packages_keyboard(plans), parse_mode="HTML")

@router.callback_query(F.data == "request_recharge")
async def cb_request_recharge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    """Handler for the 'Request Recharge' inline button shown when credits run out.
    Opens the package selection menu with active plans from database.
    """
    user_service = UserService(session)
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await callback.answer("You are not authorized.", show_alert=True)

    if callback.from_user.id in config.admin_ids:
        return await callback.answer("You are an Admin with unlimited credits! 👑", show_alert=True)

    plan_service = PlanService(session)
    plans = await plan_service.get_all_plans(active_only=True)

    text = (
        "👑 <b>Buy Credits or Unlimited Plans</b>\n\n"
        "To purchase, please contact the owner @tgekaiva.\n\n"
        "Select the package you want to buy below to send a purchase request to the admin:"
    )
    await callback.message.answer(text, reply_markup=get_payment_packages_keyboard(plans), parse_mode="HTML")
    await callback.answer()  # dismiss the button spinner


@router.callback_query(F.data.startswith("buy_plan_"))
async def cb_buy_plan(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    plan_id = int(callback.data.split("_")[2])
    
    user_service = UserService(session)
    plan_service = PlanService(session)
    
    user = await user_service.get_user_by_telegram_id(callback.from_user.id)
    if not user or user.status != UserStatus.APPROVED:
        return await callback.answer("You are not authorized.", show_alert=True)
        
    plan = await plan_service.get_plan_by_id(plan_id)
    if not plan or not plan.is_active:
        return await callback.answer("This plan is no longer available. Please select another.", show_alert=True)
        
    amount_val = plan.credits if plan.plan_type == PlanType.CREDITS else -plan.days
    req = await user_service.request_recharge(callback.from_user.id, amount_val, plan_id=plan.id)
    if not req:
        return await callback.answer(
            "You already have a pending purchase request. Please wait for the admin to process it.",
            show_alert=True
        )
        
    await callback.answer("✅ Request sent! Please contact @tgekaiva to pay.")

    pkg_name = f"{plan.name} (₹{plan.price})"
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
