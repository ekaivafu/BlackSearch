
# pyrefly: ignore [missing-import]
from aiogram import Router, F, Bot
# pyrefly: ignore [missing-import]
from aiogram.filters import Command
# pyrefly: ignore [missing-import]
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
# pyrefly: ignore [missing-import]
from aiogram.fsm.state import State, StatesGroup
# pyrefly: ignore [missing-import]
from aiogram.fsm.context import FSMContext
from bot.services.user_service import UserService
from bot.models.models import TransactionType, CreditTransaction, User, RechargeStatus, UserStatus
from bot.config import config
from bot.keyboards.inline import get_recharge_approval_keyboard
import datetime

class AdminStates(StatesGroup):
    pass

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
        elif u.credits > 0:
            plan_html = '<span class="plan-credits">Credits</span>'
            left_html = f'<span class="plan-credits">{u.credits} credits</span>'
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
        "  /unban <code>&lt;user_id&gt;</code> — Unban a user\n\n"
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
