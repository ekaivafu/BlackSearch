from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from bot.services.user_service import UserService
from bot.services.plan_service import PlanService
from bot.services.channel_service import ChannelService
from bot.models.models import TransactionType, CreditTransaction, User, RechargeStatus, UserStatus, Plan, PlanType, RequiredChannel
from bot.config import config
from bot.keyboards.inline import (
    get_recharge_approval_keyboard,
    get_admin_plans_keyboard,
    get_plan_type_selection_keyboard,
    get_plans_selection_keyboard,
    get_plan_edit_fields_keyboard,
    get_plan_delete_confirm_keyboard,
    get_admin_channels_keyboard,
    get_channels_delete_keyboard,
)
import datetime

class AdminStates(StatesGroup):
    pass

class ChannelAdminStates(StatesGroup):
    waiting_for_channel_input = State()

class PlanAdminStates(StatesGroup):
    create_value = State()
    create_price = State()
    create_name = State()
    edit_value = State()
    free_credits = State()
    daily_bonus = State()

router = Router()

def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids


def _generate_users_html(users: list) -> bytes:
    """Generate a premium dark-themed HTML report of all users."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # ── Stats ────────────────────────────────────────────────────────────────
    total    = len(users)
    approved = sum(1 for u in users if u.status == UserStatus.APPROVED)
    pending  = sum(1 for u in users if u.status == UserStatus.PENDING)
    banned   = sum(1 for u in users if u.status == UserStatus.DISABLED)
    rejected = sum(1 for u in users if u.status == UserStatus.REJECTED)
    active_subs = sum(
        1 for u in users
        if u.subscription_end and u.subscription_end > now_utc
    )

    # ── Row builder ──────────────────────────────────────────────────────────
    rows_html = []
    for i, u in enumerate(users, 1):
        name     = f"{u.first_name or ''} {u.last_name or ''}".strip() or "—"
        username = f"@{u.username}" if u.username else "—"
        uid      = str(u.telegram_user_id)
        searches = str(u.total_searches)
        joined   = u.created_at.strftime("%Y-%m-%d") if u.created_at else "—"

        # Status badge
        status_val = u.status.value if u.status else "unknown"
        badge_cls  = {
            "approved": "badge-approved",
            "pending":  "badge-pending",
            "rejected": "badge-rejected",
            "disabled": "badge-disabled",
        }.get(status_val, "badge-disabled")
        status_html = f'<span class="badge {badge_cls}">{status_val}</span>'

        # Plan & time/credits left
        has_active_sub = u.subscription_end and u.subscription_end > now_utc
        if has_active_sub:
            delta   = u.subscription_end - now_utc
            days    = delta.days
            hours   = delta.seconds // 3600
            plan_html = '<span class="plan-unlimited">&#x267e;&#xfe0f; Unlimited</span>'
            left_html = f'<span class="plan-unlimited">{days}d {hours}h left</span>'
        elif u.subscription_end and u.subscription_end <= now_utc:
            plan_html = '<span class="plan-expired">Subscription Expired</span>'
            left_html = f'<span class="plan-credits">{u.credits} credits</span>'
        else:
            bonus = getattr(u, "bonus_credits", 0) if (getattr(u, "bonus_credits_expire_at", None) and getattr(u, "bonus_credits_expire_at") > now_utc) else 0
            if u.credits > 0 or bonus > 0:
                parts = []
                if bonus > 0:
                    parts.append(f"{bonus} daily")
                if u.credits > 0:
                    parts.append(f"{u.credits} perm")
                plan_html = '<span class="plan-credits">Credits</span>'
                left_html = f'<span class="plan-credits">{" + ".join(parts)}</span>'
            else:
                plan_html = '<span class="plan-none">No Plan</span>'
                left_html = '<span class="plan-none">0</span>'

        rows_html.append(
            f'<tr>'
            f'<td>{i}</td>'
            f'<td>{name}</td>'
            f'<td class="username">{username}</td>'
            f'<td><code>{uid}</code></td>'
            f'<td>{status_html}</td>'
            f'<td>{plan_html}</td>'
            f'<td>{left_html}</td>'
            f'<td>{searches}</td>'
            f'<td>{joined}</td>'
            f'</tr>'
        )

    rows = "\n".join(rows_html) if rows_html else '<tr><td colspan="9" style="text-align:center;color:#8b949e">No users found.</td></tr>'
    generated_at = now_utc.strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>BlackSearch &#8212; User Report</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#e6edf3;padding:28px;min-height:100vh}}
    h1{{font-size:22px;color:#58a6ff;margin-bottom:4px}}
    .meta{{color:#8b949e;font-size:13px;margin-bottom:24px}}
    .stats{{display:flex;gap:14px;margin-bottom:28px;flex-wrap:wrap}}
    .sc{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 22px;min-width:110px}}
    .sc .lbl{{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:1px}}
    .sc .val{{font-size:26px;font-weight:700;margin-top:3px}}
    table{{width:100%;border-collapse:collapse;font-size:13.5px}}
    thead th{{background:#161b22;color:#8b949e;font-weight:600;padding:10px 14px;text-align:left;
              border-bottom:2px solid #30363d;text-transform:uppercase;font-size:10px;letter-spacing:.5px;
              white-space:nowrap}}
    tbody tr:hover{{background:#1c2128}}
    tbody td{{padding:10px 14px;border-bottom:1px solid #21262d;vertical-align:middle}}
    .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:700;
            text-transform:uppercase;letter-spacing:.5px}}
    .badge-approved{{background:#1a3a1a;color:#3fb950;border:1px solid #3fb950}}
    .badge-pending{{background:#3a2e00;color:#d29922;border:1px solid #d29922}}
    .badge-rejected{{background:#3a1a1a;color:#f85149;border:1px solid #f85149}}
    .badge-disabled{{background:#1c1c1c;color:#8b949e;border:1px solid #8b949e}}
    .plan-unlimited{{color:#58a6ff;font-weight:600}}
    .plan-credits{{color:#3fb950}}
    .plan-none{{color:#8b949e}}
    .plan-expired{{color:#f85149}}
    code{{background:#161b22;padding:2px 6px;border-radius:4px;font-size:11.5px;font-family:monospace;color:#79c0ff}}
    .username{{color:#58a6ff}}
  </style>
</head>
<body>
  <h1>&#128269; BlackSearch &#8212; User Report</h1>
  <p class="meta">Generated: {generated_at} UTC &nbsp;&#183;&nbsp; Total users: {total}</p>

  <div class="stats">
    <div class="sc"><div class="lbl">Total</div><div class="val">{total}</div></div>
    <div class="sc"><div class="lbl">Approved</div><div class="val" style="color:#3fb950">{approved}</div></div>
    <div class="sc"><div class="lbl">Pending</div><div class="val" style="color:#d29922">{pending}</div></div>
    <div class="sc"><div class="lbl">Rejected</div><div class="val" style="color:#f85149">{rejected}</div></div>
    <div class="sc"><div class="lbl">Banned</div><div class="val" style="color:#8b949e">{banned}</div></div>
    <div class="sc"><div class="lbl">Active Subs</div><div class="val" style="color:#58a6ff">{active_subs}</div></div>
  </div>

  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Name</th>
        <th>Username</th>
        <th>User ID</th>
        <th>Status</th>
        <th>Plan</th>
        <th>Days / Credits Left</th>
        <th>Searches</th>
        <th>Joined</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>"""
    return html.encode("utf-8")

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
async def btn_manage_users(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    user_service = UserService(session)
    users = await user_service.get_all_users()

    # ── Send HTML report ──────────────────────────────────────────────────────
    html_bytes = _generate_users_html(users)
    filename = f"users_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.html"

    await message.answer_document(
        document=BufferedInputFile(html_bytes, filename=filename),
        caption=(
            f"<b>&#128209; User Report</b>\n"
            f"Total: <b>{len(users)}</b> users\n"
            f"Open the HTML file in any browser for the full styled table."
        ),
        parse_mode="HTML"
    )

    # ── Also send the admin commands reference ────────────────────────────────
    await message.answer(
        "<b>&#9881;&#65039; Manage Users — Commands</b>\n\n"
        "New user requests are sent to you automatically for approval.\n\n"
        "<b>Dashboard &amp; Stats:</b>\n"
        "  /admin — Admin dashboard\n"
        "  /stats — Bot statistics\n\n"
        "<b>User Actions:</b>\n"
        "  /ban <code>&lt;user_id&gt;</code> — Ban a user\n"
        "  /unban <code>&lt;user_id&gt;</code> — Unban a user\n"
        "  /deleteuser <code>&lt;user_id&gt;</code> — Completely delete user (for testing restart)\n\n"
        "<b>Credits:</b>\n"
        "  /addcredit <code>&lt;user_id&gt; &lt;amount&gt;</code> — Add credits manually\n\n"
        "<b>Broadcast:</b>\n"
        "  /broadcast <code>&lt;message&gt;</code> — Message all approved users",
        parse_mode="HTML"
    )


@router.message(F.text == "💰 Manage Points")
async def btn_manage_points(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "<b>💰 Manage Points & Plans</b>\n\n"
        "• To add credits manually:\n"
        "  <code>/addcredit &lt;user_id&gt; &lt;amount&gt;</code>\n"
        "  <i>Example: /addcredit 123456789 50</i>\n\n"
        "• To manage subscription & credit packages:\n"
        "  Type <code>/plans</code> or click <b>📦 Manage Plans</b> below.\n\n"
        "• To change new user free credits:\n"
        "  <code>/freecredits</code>\n\n"
        "When users request a recharge, approval buttons appear here automatically.",
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("approve_"))
async def cb_approve_user(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    
    target_id = int(callback.data.split("_")[1])
    user_service = UserService(session)
    plan_service = PlanService(session)
    free_credits = await plan_service.get_initial_credits()
    
    user = await user_service.approve_user(target_id, callback.from_user.id, free_credits)
    if user:
        await callback.message.edit_text(f"User {target_id} approved with {free_credits} free credits.")
        try:
            await bot.send_message(target_id, f"Your access request has been approved! You have {free_credits} credits.")
        except Exception:
            pass
    else:
        await callback.message.edit_text(f"Failed to approve user {target_id}. Already processed?")
    await callback.answer()  # dismiss spinner

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
    await callback.answer()  # dismiss spinner

@router.callback_query(F.data.startswith("recharge_approve_"))
async def cb_approve_recharge(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
        
    req_id = int(callback.data.split("_")[2])
    
    user_service = UserService(session)
    plan_service = PlanService(session)
    req = await user_service.get_recharge_request(req_id)
    if not req or req.status != RechargeStatus.PENDING:
        return await callback.message.edit_text(f"Recharge #{req_id} already processed or not found.")
        
    amount = req.requested_credits
    plan = None
    if req.plan_id:
        plan = await plan_service.get_plan_by_id(req.plan_id)

    approved_req = await user_service.approve_recharge(req_id, callback.from_user.id, amount)
    
    if approved_req:
        if plan:
            pkg = f"{plan.name} (₹{plan.price})"
        elif amount == -1: pkg = "1 Day Unlimited"
        elif amount == -7: pkg = "7 Days Unlimited"
        else: pkg = f"{amount} credits"
        
        await callback.message.edit_text(f"✅ Recharge #{req_id} approved for {pkg}.")
        user = await session.get(User, req.user_id)
        if user:
            try:
                await bot.send_message(user.telegram_user_id, f"✅ Your purchase for <b>{pkg}</b> was approved! Your account is upgraded.", parse_mode="HTML")
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

@router.message(Command("deleteuser"))
async def cmd_deleteuser(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return await message.answer("Unauthorized.")
    
    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer(
            "<b>Usage:</b> <code>/deleteuser &lt;telegram_user_id&gt;</code>\n\n"
            "<i>Example: /deleteuser 123456789</i>\n\n"
            "This will completely delete the user and their logs/credits from the database so they can test the bot from scratch.",
            parse_mode="HTML"
        )
    
    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.answer("⚠️ Invalid user ID. It must be an integer Telegram ID.")
        
    if target_id == message.from_user.id:
        return await message.answer("⚠️ You cannot delete your own admin account!")

    user_service = UserService(session)
    success = await user_service.delete_user(target_id)
    if success:
        await message.answer(
            f"✅ <b>User Deleted!</b>\n\n"
            f"User <code>{target_id}</code> has been completely removed from the database.\n"
            f"They can now send /start to test the bot as a brand new user.",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"❌ User <code>{target_id}</code> was not found in the database.",
            parse_mode="HTML"
        )

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

# ─────────────────────────────────────────────────────────────────────────────
# 📦 Plan Management Dashboard & Handlers
# ─────────────────────────────────────────────────────────────────────────────

async def _build_plans_dashboard_text(session: AsyncSession) -> str:
    ps = PlanService(session)
    plans = await ps.get_all_plans(active_only=True)
    daily_bonus = await ps.get_daily_bonus_credits()

    lines = [
        "📦 <b>Plan Management Dashboard</b>\n",
        f"🎁 <b>Daily Free Bonus:</b> <code>{daily_bonus}</code> credits/day (expires at 23:59 IST)\n",
        "📋 <b>Active Subscription & Credit Plans:</b>"
    ]
    if not plans:
        lines.append("<i>No active plans found. Click '➕ Create Plan' below to add one!</i>")
    else:
        for i, p in enumerate(plans, 1):
            if p.plan_type == PlanType.CREDITS or (hasattr(p.plan_type, "value") and p.plan_type.value == "credits"):
                detail = f"{p.credits} searches"
                icon = "🪙"
            else:
                detail = f"{p.days} days unlimited"
                icon = "👑"
            lines.append(f"{i}. {icon} <b>{p.name}</b> — <b>₹{p.price}</b> (<code>{detail}</code>) [ID: <code>{p.id}</code>]")

    lines.append(
        "\n<i>Quick Commands:</i>\n"
        "• /createplan — Create a new plan\n"
        "• /editplan — Edit an existing plan\n"
        "• /deleteplan — Delete a plan\n"
        "• /dailybonus — Edit daily bonus credits"
    )
    return "\n".join(lines)

@router.message(F.text == "📦 Manage Plans")
@router.message(Command("plans"))
@router.message(Command("manageplans"))
async def cmd_manage_plans(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    text = await _build_plans_dashboard_text(session)
    await message.answer(text, reply_markup=get_admin_plans_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "admin_plans_refresh")
async def cb_plans_refresh(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    text = await _build_plans_dashboard_text(session)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_plans_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_plans_keyboard(), parse_mode="HTML")
    await callback.answer("Dashboard refreshed")

@router.callback_query(F.data == "admin_plan_close")
async def cb_plan_close(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("Closed.")
    await callback.answer()

@router.callback_query(F.data == "plan_cancel")
async def cb_plan_cancel(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    text = await _build_plans_dashboard_text(session)
    await callback.message.edit_text(text, reply_markup=get_admin_plans_keyboard(), parse_mode="HTML")
    await callback.answer("Cancelled.")

# ── ➕ CREATE PLAN ────────────────────────────────────────────────────────────

@router.message(Command("createplan"))
async def cmd_createplan(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "➕ <b>Create a New Plan</b>\n\nChoose the plan type:",
        reply_markup=get_plan_type_selection_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_plan_create")
async def cb_plan_create(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    await callback.message.edit_text(
        "➕ <b>Create a New Plan</b>\n\nChoose the plan type:",
        reply_markup=get_plan_type_selection_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "create_type_credits")
async def cb_create_type_credits(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.update_data(plan_type="credits")
    await state.set_state(PlanAdminStates.create_value)
    await callback.message.edit_text(
        "🪙 <b>Step 1/3: Number of Searches / Credits</b>\n\n"
        "How many search credits should this plan grant to the user?\n"
        "<i>Example: 20, 50, 100</i>\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "create_type_days")
async def cb_create_type_days(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.update_data(plan_type="days")
    await state.set_state(PlanAdminStates.create_value)
    await callback.message.edit_text(
        "👑 <b>Step 1/3: Duration in Days</b>\n\n"
        "How many days of <b>Unlimited Searches</b> should this plan grant?\n"
        "<i>Example: 1, 7, 30</i>\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(PlanAdminStates.create_value)
async def process_create_plan_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Creation cancelled.")
    if not text.isdigit() or int(text) <= 0:
        return await message.answer("⚠️ Please enter a valid positive number (e.g. 15, 30):")
    
    val = int(text)
    data = await state.get_data()
    plan_type = data.get("plan_type", "credits")
    await state.update_data(amount=val)
    await state.set_state(PlanAdminStates.create_price)

    type_desc = f"{val} searches" if plan_type == "credits" else f"{val} days unlimited"
    await message.answer(
        f"✅ Selected: <b>{type_desc}</b>\n\n"
        "💰 <b>Step 2/3: Price in ₹</b>\n"
        "Enter the price for this plan in ₹ (INR):\n"
        "<i>Example: 50, 100, 499</i>",
        parse_mode="HTML"
    )

@router.message(PlanAdminStates.create_price)
async def process_create_plan_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Creation cancelled.")
    if not text.isdigit() or int(text) < 0:
        return await message.answer("⚠️ Please enter a valid non-negative number for price in ₹ (e.g. 100):")
    
    price = int(text)
    data = await state.get_data()
    plan_type = data.get("plan_type", "credits")
    amount = data.get("amount", 1)
    
    if plan_type == "credits":
        default_name = f"₹{price} for {amount} searches"
    else:
        unit = "day" if amount == 1 else "days"
        default_name = f"₹{price} for {amount} {unit} unlimited"
        
    await state.update_data(price=price, default_name=default_name)
    await state.set_state(PlanAdminStates.create_name)

    await message.answer(
        f"💰 Price set to: <b>₹{price}</b>\n\n"
        "🏷️ <b>Step 3/3: Plan Display Title</b>\n"
        f"Default title: <code>{default_name}</code>\n\n"
        "Send <b>/skip</b> to use the default title, or type your own custom title (e.g. <i>Weekend Pass</i>):",
        parse_mode="HTML"
    )

@router.message(PlanAdminStates.create_name)
async def process_create_plan_name(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Creation cancelled.")
        
    data = await state.get_data()
    plan_type_str = data.get("plan_type", "credits")
    amount = data.get("amount", 0)
    price = data.get("price", 0)
    default_name = data.get("default_name", "Package")
    
    final_name = default_name if text == "/skip" else text
    await state.clear()
    
    ps = PlanService(session)
    pt = PlanType.CREDITS if plan_type_str == "credits" else PlanType.DAYS
    credits_val = amount if pt == PlanType.CREDITS else 0
    days_val = amount if pt == PlanType.DAYS else 0
    
    plan = await ps.create_plan(
        name=final_name,
        plan_type=pt,
        credits=credits_val,
        days=days_val,
        price=price
    )
    
    type_line = f"🪙 <b>Credits:</b> {plan.credits} searches" if pt == PlanType.CREDITS else f"👑 <b>Duration:</b> {plan.days} days unlimited"
    await message.answer(
        "🎉 <b>New Plan Successfully Created!</b>\n\n"
        f"🏷️ <b>Title:</b> {plan.name}\n"
        f"💎 <b>Type:</b> {pt.value.capitalize()}\n"
        f"{type_line}\n"
        f"💰 <b>Price:</b> ₹{plan.price}\n\n"
        "<i>This package is now available for users in /recharge.</i>",
        reply_markup=get_admin_plans_keyboard(),
        parse_mode="HTML"
    )

# ── ✏️ EDIT PLAN ──────────────────────────────────────────────────────────────

@router.message(Command("editplan"))
@router.message(Command("edit"))
async def cmd_editplan(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    ps = PlanService(session)
    plans = await ps.get_all_plans(active_only=True)
    if not plans:
        return await message.answer("❌ No active plans found to edit.")
    await message.answer(
        "✏️ <b>Select a plan to edit:</b>",
        reply_markup=get_plans_selection_keyboard(plans, "admin_edit_plan"),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_plan_list_edit")
async def cb_plan_list_edit(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    ps = PlanService(session)
    plans = await ps.get_all_plans(active_only=True)
    if not plans:
        return await callback.message.edit_text(
            "❌ No active plans found to edit.",
            reply_markup=get_admin_plans_keyboard()
        )
    await callback.message.edit_text(
        "✏️ <b>Select a plan to edit:</b>",
        reply_markup=get_plans_selection_keyboard(plans, "admin_edit_plan"),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("admin_edit_plan_"))
async def cb_edit_plan_select(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    plan_id = int(callback.data.split("_")[3])
    ps = PlanService(session)
    plan = await ps.get_plan_by_id(plan_id)
    if not plan or not plan.is_active:
        return await callback.answer("Plan not found.", show_alert=True)
        
    is_cred = plan.plan_type == PlanType.CREDITS or (hasattr(plan.plan_type, "value") and plan.plan_type.value == "credits")
    detail = f"{plan.credits} searches" if is_cred else f"{plan.days} days unlimited"
    
    text = (
        f"✏️ <b>Edit Plan #{plan.id}</b>\n\n"
        f"🏷️ <b>Name:</b> {plan.name}\n"
        f"💎 <b>Type:</b> {plan.plan_type.value.capitalize()} ({detail})\n"
        f"💰 <b>Price:</b> ₹{plan.price}\n\n"
        "Choose what you would like to edit below:"
    )
    await callback.message.edit_text(text, reply_markup=get_plan_edit_fields_keyboard(plan), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("plan_field_"))
async def cb_plan_field_select(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    parts = callback.data.split("_")
    field = parts[2] # "name", "price", "val"
    plan_id = int(parts[3])
    
    ps = PlanService(session)
    plan = await ps.get_plan_by_id(plan_id)
    if not plan or not plan.is_active:
        return await callback.answer("Plan not found.", show_alert=True)
        
    await state.update_data(edit_plan_id=plan_id, edit_field=field)
    await state.set_state(PlanAdminStates.edit_value)
    
    if field == "name":
        prompt = f"🏷️ Current name: <code>{plan.name}</code>\n\nEnter the new display title for this plan:"
    elif field == "price":
        prompt = f"💰 Current price: <b>₹{plan.price}</b>\n\nEnter the new price in ₹ (e.g. 150):"
    else: # "val"
        is_cred = plan.plan_type == PlanType.CREDITS or (hasattr(plan.plan_type, "value") and plan.plan_type.value == "credits")
        if is_cred:
            prompt = f"🪙 Current credits: <b>{plan.credits}</b> searches\n\nEnter the new number of search credits:"
        else:
            prompt = f"👑 Current duration: <b>{plan.days}</b> days\n\nEnter the new duration in days:"
            
    prompt += "\n\n<i>Send /cancel to abort.</i>"
    await callback.message.edit_text(prompt, parse_mode="HTML")
    await callback.answer()

@router.message(PlanAdminStates.edit_value)
async def process_edit_plan_value(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Edit cancelled.")
        
    data = await state.get_data()
    plan_id = data.get("edit_plan_id")
    field = data.get("edit_field")
    await state.clear()
    
    ps = PlanService(session)
    plan = await ps.get_plan_by_id(plan_id)
    if not plan:
        return await message.answer("❌ Plan not found.")
        
    is_cred = plan.plan_type == PlanType.CREDITS or (hasattr(plan.plan_type, "value") and plan.plan_type.value == "credits")
    
    if field == "name":
        await ps.update_plan(plan_id, name=text)
    elif field == "price":
        if not text.isdigit() or int(text) < 0:
            return await message.answer("⚠️ Invalid price. Must be a non-negative number.")
        await ps.update_plan(plan_id, price=int(text))
    elif field == "val":
        if not text.isdigit() or int(text) <= 0:
            return await message.answer("⚠️ Invalid number. Must be a positive integer.")
        if is_cred:
            await ps.update_plan(plan_id, credits=int(text))
        else:
            await ps.update_plan(plan_id, days=int(text))
            
    updated_plan = await ps.get_plan_by_id(plan_id)
    detail = f"{updated_plan.credits} searches" if is_cred else f"{updated_plan.days} days unlimited"
    
    await message.answer(
        f"✅ <b>Plan Updated Successfully!</b>\n\n"
        f"🏷️ <b>Name:</b> {updated_plan.name}\n"
        f"💎 <b>Type:</b> {updated_plan.plan_type.value.capitalize()} ({detail})\n"
        f"💰 <b>Price:</b> ₹{updated_plan.price}",
        reply_markup=get_plan_edit_fields_keyboard(updated_plan),
        parse_mode="HTML"
    )

# ── 🗑️ DELETE PLAN ────────────────────────────────────────────────────────────

@router.message(Command("deleteplan"))
@router.message(Command("delete"))
async def cmd_deleteplan(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    ps = PlanService(session)
    plans = await ps.get_all_plans(active_only=True)
    if not plans:
        return await message.answer("❌ No active plans found to delete.")
    await message.answer(
        "🗑️ <b>Select a plan to delete:</b>",
        reply_markup=get_plans_selection_keyboard(plans, "admin_del_plan"),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_plan_list_delete")
async def cb_plan_list_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    ps = PlanService(session)
    plans = await ps.get_all_plans(active_only=True)
    if not plans:
        return await callback.message.edit_text(
            "❌ No active plans found to delete.",
            reply_markup=get_admin_plans_keyboard()
        )
    await callback.message.edit_text(
        "🗑️ <b>Select a plan to delete:</b>",
        reply_markup=get_plans_selection_keyboard(plans, "admin_del_plan"),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("admin_del_plan_"))
async def cb_delete_plan_select(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    plan_id = int(callback.data.split("_")[3])
    ps = PlanService(session)
    plan = await ps.get_plan_by_id(plan_id)
    if not plan or not plan.is_active:
        return await callback.answer("Plan not found.", show_alert=True)
        
    await callback.message.edit_text(
        f"⚠️ <b>Are you sure you want to delete this plan?</b>\n\n"
        f"📦 <b>{plan.name}</b> — ₹{plan.price}\n\n"
        "<i>Users will immediately stop seeing this plan in /recharge.</i>",
        reply_markup=get_plan_delete_confirm_keyboard(plan.id),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("do_delete_plan_"))
async def cb_do_delete_plan(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    plan_id = int(callback.data.split("_")[3])
    ps = PlanService(session)
    plan = await ps.get_plan_by_id(plan_id)
    name = plan.name if plan else f"#{plan_id}"
    await ps.delete_plan(plan_id)
    
    text = f"✅ Plan <b>{name}</b> has been deleted.\n\n" + await _build_plans_dashboard_text(session)
    await callback.message.edit_text(text, reply_markup=get_admin_plans_keyboard(), parse_mode="HTML")
    await callback.answer("Plan deleted!")

# ── 🎁 EDIT FREE CREDITS FOR NEW USERS ───────────────────────────────────────

@router.message(Command("freecredits"))
async def cmd_freecredits(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    ps = PlanService(session)
    cur_cred = await ps.get_initial_credits()
    await state.set_state(PlanAdminStates.free_credits)
    await message.answer(
        f"🎁 <b>Edit New User Free Credits</b>\n\n"
        f"Currently, newly approved/registered users receive: <code>{cur_cred}</code> free credits.\n\n"
        "Send the new number of free credits to give to new users:\n"
        "<i>(Enter 0 for no free credits, or 5, 10, etc.)\nSend /cancel to abort.</i>",
        parse_mode="HTML"
    )

@router.message(Command("dailybonus"))
@router.callback_query(F.data == "admin_edit_daily_bonus")
async def cb_edit_daily_bonus(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext):
    user_id = event.from_user.id
    if not is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("Unauthorized.", show_alert=True)
        return
    await state.clear()
    ps = PlanService(session)
    cur_bonus = await ps.get_daily_bonus_credits()
    await state.set_state(PlanAdminStates.daily_bonus)
    text = (
        "🎁 <b>Edit Daily Bonus Credits</b>\n\n"
        f"Currently, users receive: <code>{cur_bonus}</code> credits on their 1st message of each day.\n"
        "<i>These credits expire at 23:59 IST daily.</i>\n\n"
        "Send the new number of daily bonus credits to give to users:\n"
        "<i>(Enter 0 to disable daily bonus, or 1, 3, 5, etc.)\nSend /cancel to abort.</i>"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML")

@router.message(PlanAdminStates.daily_bonus)
async def process_daily_bonus_value(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Operation cancelled.")
    if not text.isdigit() or int(text) < 0:
        return await message.answer("⚠️ Please enter a valid non-negative number (e.g. 0, 3, 5):")

    val = int(text)
    await state.clear()
    ps = PlanService(session)
    await ps.set_daily_bonus_credits(val)

    await message.answer(
        f"✅ <b>Daily Bonus Updated!</b>\n\n"
        f"All users will now receive <b>{val}</b> bonus search credits on their 1st interaction of each day (valid until 23:59 IST).",
        reply_markup=get_admin_plans_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("freecredits"))
@router.callback_query(F.data == "admin_edit_free_credits")
async def cb_edit_free_credits(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext):
    await cb_edit_daily_bonus(event, session, state)

@router.message(PlanAdminStates.free_credits)
async def process_free_credits_value(message: Message, session: AsyncSession, state: FSMContext):
    await process_daily_bonus_value(message, session, state)

# ─────────────────────────────────────────────────────────────────────────────
# 📢 Required Channels Management (Force Subscription)
# ─────────────────────────────────────────────────────────────────────────────

async def _build_channels_dashboard_text(session: AsyncSession) -> str:
    cs = ChannelService(session)
    channels = await cs.get_active_channels()

    lines = [
        "📢 <b>Required Channels Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Every user (existing or new) must join these channels to access the bot.\n"
    ]
    if not channels:
        lines.append("<i>ℹ️ No required channels are currently configured. The bot is open to all users.</i>\n")
    else:
        lines.append("<b>Active Required Channels:</b>")
        for i, ch in enumerate(channels, 1):
            user_part = f" ({ch.username})" if ch.username else ""
            lines.append(
                f"{i}. 📢 <b>{ch.title}</b>{user_part}\n"
                f"   🔗 <code>{ch.invite_link}</code>\n"
                f"   🆔 <code>{ch.channel_id}</code>"
            )
        lines.append("")

    lines.append(
        "<i>Quick Commands:</i>\n"
        "• /channels — View this dashboard\n"
        "• /addchannel — Add a required channel\n"
        "• /delchannel — Remove a required channel"
    )
    return "\n".join(lines)

@router.message(F.text == "📢 Channels")
@router.message(Command("channels"))
async def cmd_manage_channels(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    text = await _build_channels_dashboard_text(session)
    await message.answer(text, reply_markup=get_admin_channels_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "admin_ch_refresh")
async def cb_channels_refresh(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    text = await _build_channels_dashboard_text(session)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_channels_keyboard(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_channels_keyboard(), parse_mode="HTML")
    await callback.answer("Dashboard refreshed")

@router.callback_query(F.data == "admin_ch_close")
async def cb_channels_close(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_text("Closed.")
    await callback.answer()

@router.message(Command("addchannel"))
@router.callback_query(F.data == "admin_ch_add")
async def cmd_add_channel(event: Message | CallbackQuery, bot: Bot, state: FSMContext):
    user_id = event.from_user.id
    if not is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("Unauthorized.", show_alert=True)
        return

    await state.clear()
    me = await bot.get_me()
    bot_username = f"@{me.username}" if me.username else "this bot"

    text = (
        "➕ <b>Add Required Channel / Group</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>CRITICAL PREREQUISITE:</b>\n"
        f"You <b>MUST add {bot_username} as an Administrator</b> in your channel/group first!\n\n"
        "<i>Without admin privileges, Telegram will NOT permit the bot to check member statuses.</i>\n\n"
        "👉 <b>Now send the channel:</b>\n"
        "• Its public <b>@username</b> (e.g. <code>@MyChannel</code>)\n"
        "• OR its numerical <b>Chat ID</b> (e.g. <code>-1001234567890</code>)\n\n"
        "<i>Send /cancel to abort.</i>"
    )

    await state.set_state(ChannelAdminStates.waiting_for_channel_input)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML")

@router.message(ChannelAdminStates.waiting_for_channel_input)
async def process_channel_input(message: Message, bot: Bot, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = message.text.strip()
    if text == "/cancel":
        await state.clear()
        return await message.answer("Adding channel cancelled.")

    # Try to parse channel identifier
    identifier = text
    if identifier.lstrip("-").isdigit():
        identifier = int(identifier)

    # Verify bot is an admin in the channel
    is_admin_in_chat, err_msg, info = await ChannelService.verify_bot_admin_status(bot, identifier)
    if not is_admin_in_chat:
        return await message.answer(
            f"❌ <b>Verification Failed:</b>\n\n{err_msg}\n\n"
            "Please make sure the bot is an Administrator in the channel, then send the @username or ID again, or send /cancel.",
            parse_mode="HTML"
        )

    # Save to database
    cs = ChannelService(session)
    ch = await cs.add_or_update_channel(
        channel_id=info["channel_id"],
        title=info["title"],
        invite_link=info["invite_link"],
        username=info.get("username")
    )
    await state.clear()

    await message.answer(
        "🎉 <b>Channel Successfully Added!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📢 <b>Title:</b> {ch.title}\n"
        f"🔗 <b>Invite Link:</b> {ch.invite_link}\n"
        f"🆔 <b>Chat ID:</b> <code>{ch.channel_id}</code>\n\n"
        "🛡️ Bot status: <b>Verified Administrator ✅</b>\n\n"
        "<i>All non-admin users must now join this channel before they can use the bot.</i>",
        reply_markup=get_admin_channels_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("delchannel"))
@router.callback_query(F.data == "admin_ch_del_list")
async def cmd_del_channel(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext):
    user_id = event.from_user.id
    if not is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("Unauthorized.", show_alert=True)
        return

    await state.clear()
    cs = ChannelService(session)
    channels = await cs.get_active_channels()
    if not channels:
        msg = "❌ No active required channels found to delete."
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(msg, reply_markup=get_admin_channels_keyboard())
            await event.answer()
        else:
            await event.answer(msg)
        return

    text = "🗑️ <b>Select a channel to remove from requirements:</b>"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=get_channels_delete_keyboard(channels), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=get_channels_delete_keyboard(channels), parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_ch_del_"))
async def cb_delete_channel(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Unauthorized.", show_alert=True)

    record_id = int(callback.data.split("_")[3])
    cs = ChannelService(session)
    ch = await cs.get_channel_by_id(record_id)
    title = ch.title if ch else f"#{record_id}"

    await cs.delete_channel(record_id)
    await callback.answer(f"Deleted {title}!")

    text = f"✅ Channel <b>{title}</b> removed from requirements.\n\n" + await _build_channels_dashboard_text(session)
    await callback.message.edit_text(text, reply_markup=get_admin_channels_keyboard(), parse_mode="HTML")


